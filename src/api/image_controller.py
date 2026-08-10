"""
Controller for image printing API endpoints.
"""

import os
import json
import uuid
import structlog
from typing import Dict, Any
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from flask import g, request, current_app
from PIL import Image, UnidentifiedImageError

from src.services.printer_service import printer_service
from src.services.queue_service import print_queue
from src.services.settings_service import settings_service
from src.utils.exceptions import ValidationError, PrinterError, ImageProcessingError, ResourceNotFoundError, ConfirmationRequiredError
from src.utils.print_guard import guard_and_dispatch
from src.utils.dry_run import is_dry_run, build_dry_run_response

logger = structlog.get_logger()

# Guard against decompression-bomb DoS: cap the number of pixels Pillow will
# decode from an uploaded image.
Image.MAX_IMAGE_PIXELS = 50_000_000

def stash_raw_body():
    """Read a raw image body BEFORE anything can consume the stream.

    Wired as a before_request hook (see src/app.py), because by the time the
    handler runs connexion has already destroyed the body. Its ConnexionRequest
    is built as

        form=flask_request.form,        # <- parses the form, CONSUMES the stream
        ...
        body=flask_request.get_data(),  # <- now returns b""

    with `form` evaluated before `body`, so any request whose content type
    Werkzeug treats as a form arrives at the handler empty. BusyBox wget sends
    application/x-www-form-urlencoded by default, which is exactly that case.

    Reading here caches the body on the request, so the later reads see it.
    """
    if request.method != "POST" or not request.path.endswith("/image/print"):
        return None
    if request.mimetype == "multipart/form-data":
        return None
    # cache=True: the point is to populate Werkzeug's cache so that connexion's
    # own get_data() -- and ours -- return the bytes rather than nothing.
    data = request.get_data(cache=True)
    if data and _looks_like_image(data):
        g.raw_image_body = data
    return None


def _read_raw_image_body():
    """Return the request body when the image arrived as raw bytes, else None.

    Prefers what stash_raw_body cached: by the time this runs connexion has
    already parsed the form, and on a form-typed content type that leaves the
    stream empty.

    Lets callers that cannot build a multipart upload print an image. That is
    not a hypothetical: Homebox's HBOX_LABEL_MAKER_PRINT_COMMAND is a single
    shell command, and its image ships only BusyBox wget -- no curl, no
    multipart. Requiring multipart forced a relay service to exist purely to
    re-encode the body.

    ORDER MATTERS, and getting it wrong fails silently. Touching request.files
    or request.form makes Werkzeug parse the body as a form and CONSUME the
    stream, after which get_data() returns b"". BusyBox wget sends
    Content-Type: application/x-www-form-urlencoded by default, so a caller
    that looked for a file field first swallowed every raw upload: the magic
    check then saw zero bytes. Worse, wget exits 0 on an HTTP error unless
    --server-response is passed, so the sender reported success.

    So: decide from the CONTENT TYPE alone, before reading anything.
    """
    # A real multipart upload is handled by the normal path.
    if request.mimetype == "multipart/form-data":
        return None

    stashed = getattr(g, "raw_image_body", None)
    if stashed:
        return stashed

    # Fallback for callers that reach this without the hook (tests build a bare
    # request context). Works whenever the stream is still intact.
    data = request.get_data(cache=True)
    if not data:
        return None

    # Only accept something that actually looks like an image. Anything else is
    # far more likely to be a mis-sent form than a picture, and treating it as
    # one produces a confusing "not a valid image" much later.
    if not _looks_like_image(data):
        return None

    return data


# Magic numbers for the formats Pillow will accept here. PNG covers Homebox and
# the browser uploader; the rest are cheap to allow and would otherwise be a
# baffling rejection.
_IMAGE_MAGIC = (
    b"\x89PNG\r\n\x1a\n",       # PNG
    b"\xff\xd8\xff",            # JPEG
    b"GIF87a", b"GIF89a",       # GIF
    b"BM",                      # BMP
    b"II*\x00", b"MM\x00*",     # TIFF
)


def _looks_like_image(data: bytes) -> bool:
    return any(data.startswith(m) for m in _IMAGE_MAGIC)


def _opt(name: str):
    """Read an option from the form or the query string.

    A raw-body caller has no form to put flags in, so they arrive as query
    parameters: ...?hold=true&settings=%7B...%7D
    """
    if name in request.form:
        return request.form.get(name)
    return request.args.get(name)


def _save_raw_body(data: bytes) -> str:
    """Persist a raw-body image the same way an upload is persisted."""
    jobs_dir = os.path.join(_get_upload_folder(), "jobs")
    os.makedirs(jobs_dir, exist_ok=True)
    path = os.path.join(jobs_dir, f"{uuid.uuid4().hex}.png")
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def print_image() -> Dict[str, Any]:
    """
    Print an image on a label.

    Accepts either a multipart upload (``image`` file field, options in the
    form) or a raw image body (options in the query string). See
    _read_raw_image_body for why both exist.

    Returns:
        Dict containing the result of the print operation.
    """
    try:
        logger.info("Processing image print request")

        # Two body shapes. ORDER MATTERS -- see _read_raw_image_body.
        raw = _read_raw_image_body()

        if raw is None:
            # Multipart: the browser uploader and anything that can build one.
            if 'image' not in request.files:
                raise ValidationError("No image file provided", "image")

            image_file = request.files['image']
            if image_file.filename == '':
                raise ValidationError("No image file selected", "image")

            settings_json = request.form.get('settings', '{}')
        else:
            # Raw body: minimal clients that cannot do multipart uploads.
            image_file = None
            settings_json = request.args.get('settings', '{}')

        try:
            settings = settings_service.resolve_print_settings(json.loads(settings_json))
        except json.JSONDecodeError:
            raise ValidationError("Invalid settings JSON", "settings")
        
        # Validate required settings
        required_settings = ["printer_uri", "printer_model", "label_size"]
        for setting in required_settings:
            if setting not in settings:
                raise ValidationError(f"{setting} is required", f"settings.{setting}")

        # Dry run: validate settings + reachability, but do not save/print.
        if is_dry_run(_opt("dry_run")):
            return build_dry_run_response(settings, None)

        # Persist the uploaded image under uploads/jobs/ so it survives the
        # print and is available for reprint/open. TTL cleanup in the queue
        # service removes it later -- the job no longer deletes it.
        stored_path = (_save_raw_body(raw) if image_file is None
                       else _save_uploaded_file(image_file))
        logger.info("Image saved", path=stored_path)

        # Verify the uploaded file is actually a decodable image before
        # enqueuing it. Image.verify() consumes the file object, so we
        # re-open for each step. On rejection we clean up the just-saved file
        # immediately, since nothing was queued.
        try:
            with Image.open(stored_path) as img:
                img.verify()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
            logger.warning("Rejected non-image or invalid upload", error=str(e))
            _cleanup_uploaded_file(stored_path)
            raise ValidationError("Uploaded file is not a valid image", "image")

        # Enqueue the print job. The job prints from the persistent path and
        # does NOT delete it; TTL cleanup handles removal. Default args bind
        # the current path/settings to avoid late-binding in the closure.
        def job(path=stored_path, s=settings):
            printer_service.print_image(path, s)

        original_name = (image_file.filename if image_file else None) or "Image"
        label = secure_filename(original_name) or "Image"
        params = {"type": "image", "filename": original_name, "settings": settings}
        # An image job holds its file, so a held one can ALSO be reopened in
        # the composer -- which is what /image/compose has always done.
        return guard_and_dispatch(
            "image", label, job, settings,
            hold=_opt("hold"),
            confirm_large_batch=_opt("confirm_large_batch"),
            amend_job_id=_opt("amend_job_id"),
            params=params, file_path=stored_path,
        )
    except ConfirmationRequiredError:
        raise
    except ValidationError as e:
        logger.error("Validation error", error=str(e), exc_info=True)
        raise
    except PrinterError as e:
        logger.error("Printer error", error=str(e), exc_info=True)
        raise
    except ImageProcessingError as e:
        logger.error("Image processing error", error=str(e), exc_info=True)
        raise
    except ResourceNotFoundError as e:
        logger.error("Resource not found", error=str(e), exc_info=True)
        raise
    except ValueError as e:
        # Pure input/validation errors from the service layer must map to
        # HTTP 400, not 500.
        logger.warning("Validation error", error=str(e), exc_info=True)
        raise ValidationError(str(e), "settings")
    except Exception as e:
        logger.error("Error printing image", error=str(e), exc_info=True)
        raise PrinterError(f"Error printing image: {str(e)}")

def compose_image() -> Dict[str, Any]:
    """Accept an image and HOLD it for review instead of printing.

    For callers that render a label and fire it at the printer with no way to
    check the result first -- Homebox being the case this exists for. Its
    PRINT_COMMAND is fire-and-forget: it cannot open a browser, and it has no
    idea whether the rotation or scaling is what the user actually wanted.

    Same shape as the Canva "open in composer" flow: nothing prints, the image
    is persisted as a job, and the editor can open it, adjust rotation and
    scale, and print or discard. See export_canva_design for the reasoning about
    why the queue's global pause cannot be used for this.

    NO media guard: nothing is printed, and refusing to accept a label because
    the wrong roll is loaded would defeat the point -- holding it for review is
    exactly when the roll is most likely still to be changed.
    """
    if 'image' not in request.files:
        raise ValidationError("No image file provided", "image")

    image_file = request.files['image']
    if image_file.filename == '':
        raise ValidationError("No image file selected", "image")

    settings_json = request.form.get('settings', '{}')
    try:
        settings = settings_service.resolve_print_settings(json.loads(settings_json))
    except json.JSONDecodeError:
        raise ValidationError("Invalid settings JSON", "settings")

    original_name = image_file.filename or "Image"
    stored_path = _save_uploaded_file(image_file)

    try:
        with Image.open(stored_path) as img:
            img.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
        logger.warning("Rejected non-image or invalid upload", error=str(e))
        _cleanup_uploaded_file(stored_path)
        raise ValidationError("Uploaded file is not a valid image", "image")

    label = (request.form.get('label') or request.args.get('label')
             or secure_filename(original_name) or "Image")
    params = {"type": "image", "filename": original_name, "settings": settings}
    job_id = print_queue.hold("image", label, params=params, file_path=stored_path)
    logger.info("Image held for review", job_id=job_id, path=stored_path)

    return {
        "success": True,
        "job_id": job_id,
        "held": True,
        "message": "Label held for review -- open it in the composer to print",
    }


def _save_uploaded_file(file: FileStorage) -> str:
    """
    Save an uploaded file to the upload folder.
    
    Args:
        file: The uploaded file.
        
    Returns:
        Path to the saved file.
    """
    # Sanitize the original filename to prevent path traversal. secure_filename
    # may return an empty string (e.g. for names made up entirely of unsafe
    # characters), so we only keep its extension and always prefix a UUID.
    safe_name = secure_filename(file.filename or "")
    extension = os.path.splitext(safe_name)[1]
    filename = f"{uuid.uuid4().hex}{extension}"

    # Persist into the uploads/jobs/ subfolder so queued jobs keep their file
    # around for reprint/open until the queue service's TTL cleanup removes it.
    jobs_folder = os.path.join(_get_upload_folder(), "jobs")
    os.makedirs(jobs_folder, exist_ok=True)

    # Save the file
    file_path = os.path.join(jobs_folder, filename)
    file.save(file_path)

    return file_path


def _get_upload_folder() -> str:
    """Return the configured upload folder, falling back to the default.

    Resolves from app config first, then printer_service.upload_folder (the
    single source of truth honouring the UPLOAD_FOLDER env var), then the
    historical code-relative default as a last resort.
    """
    try:
        upload_folder = current_app.config.get('UPLOAD_FOLDER')
    except RuntimeError:
        upload_folder = None
    if not upload_folder:
        upload_folder = getattr(printer_service, "upload_folder", None)
    if not upload_folder:
        upload_folder = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads"
        )
    return upload_folder


def _cleanup_uploaded_file(file_path: str) -> None:
    """
    Remove the uploaded source file after processing.

    Args:
        file_path: Path to the saved upload to delete.
    """
    if not file_path:
        return
    try:
        os.remove(file_path)
        logger.info("Cleaned up uploaded file", path=file_path)
    except FileNotFoundError:
        pass
    except OSError as e:
        # Cleanup failure must not break the print response.
        logger.warning("Failed to clean up uploaded file", path=file_path, error=str(e))
