"""
Controller for browsing Canva and printing a design.

A design is exported straight into ``uploads/jobs/`` -- the same place an
uploaded image lands -- and then enqueued through the same path as any image
print. That is deliberate: everything the queue already does for an upload
(reprint, "open" in the composer, TTL cleanup) then works for a Canva design for
free, with no Canva-specific plumbing in the queue.

Listing costs nothing. Canva returns a thumbnail URL with folder items, so the
browser shows designs without exporting them; only printing spends export quota.
"""

import base64
import os
import uuid
from typing import Any, Dict

import structlog

from src.services.canva_service import (
    CanvaNotConfigured,
    CanvaNotConnected,
    canva_service,
)
from src.services.printer_service import printer_service
from src.services.queue_service import print_queue
from src.services.settings_service import settings_service
from src.utils.dry_run import build_dry_run_response, is_dry_run
from src.utils.exceptions import PrinterError, ValidationError
from src.utils.print_guard import (
    enforce_large_batch_confirmation,
    enforce_media_match,
    is_confirmed,
)

logger = structlog.get_logger()


def _short_label(title: str, limit: int = 40) -> str:
    """Build a short, single-line human label for a queued job."""
    flattened = " ".join((title or "Canva design").split())
    if len(flattened) > limit:
        return flattened[:limit].rstrip() + "..."
    return flattened


def get_canva_status() -> Dict[str, Any]:
    """Report whether Canva browsing is available.

    Never raises: the UI calls this to decide whether to show the tab at all, so
    "not configured" and "not connected" are normal answers rather than errors.
    """
    if not canva_service.is_configured():
        return {
            "configured": False,
            "connected": False,
            "message": "Set canva_broker_url in Settings to browse Canva.",
        }
    try:
        canva_service._access_token()
        return {"configured": True, "connected": True}
    except CanvaNotConnected as exc:
        return {"configured": True, "connected": False, "message": str(exc)}


def list_canva_folders(parent: str = "root") -> Dict[str, Any]:
    """List sub-folders, for the folder picker."""
    try:
        return {"folders": canva_service.list_folders(parent)}
    except CanvaNotConfigured as exc:
        raise ValidationError(str(exc), "canva_broker_url") from exc
    except CanvaNotConnected as exc:
        raise PrinterError(str(exc)) from exc
    except Exception as exc:
        logger.error("Error listing Canva folders", error=str(exc), exc_info=True)
        raise PrinterError(f"Error listing Canva folders: {exc}") from exc


def list_canva_designs(folder_id: str) -> Dict[str, Any]:
    """List the designs in a folder."""
    if not folder_id:
        raise ValidationError("folder_id is required", "folder_id")
    try:
        return {"designs": canva_service.list_folder(folder_id)}
    except CanvaNotConfigured as exc:
        raise ValidationError(str(exc), "canva_broker_url") from exc
    except CanvaNotConnected as exc:
        raise PrinterError(str(exc)) from exc
    except Exception as exc:
        logger.error("Error listing Canva designs", error=str(exc), exc_info=True)
        raise PrinterError(f"Error listing Canva designs: {exc}") from exc


def print_canva_design(body: Dict[str, Any]) -> Dict[str, Any]:
    """Export a Canva design and queue it for printing.

    The export lands in ``uploads/jobs/`` and is then enqueued exactly as an
    uploaded image would be, so the queue's reprint/open/TTL behaviour applies
    unchanged.
    """
    design_id = (body or {}).get("design_id")
    if not design_id:
        raise ValidationError("design_id is required", "design_id")

    title = (body or {}).get("title") or "Canva design"
    settings = settings_service.resolve_print_settings((body or {}).get("settings"))

    for required in ("printer_uri", "printer_model", "label_size"):
        if required not in settings:
            raise ValidationError(f"{required} is required", f"settings.{required}")

    # Reject a label size the loaded media cannot print BEFORE spending an
    # export. Printing is asynchronous, so an error raised later never reaches
    # the caller -- and an export burned on a job that cannot print is worse than
    # one that is merely queued.
    enforce_media_match(settings)
    enforce_large_batch_confirmation(
        settings.get("copies", 1),
        is_confirmed((body or {}).get("confirm_large_batch")),
    )

    # Dry run: validate everything, but do not export or print. Checked after the
    # guards so a dry run reports the same rejections a real print would.
    if is_dry_run((body or {}).get("dry_run")):
        return build_dry_run_response(settings, None)

    try:
        image_bytes = canva_service.export_design_png(design_id)
    except CanvaNotConfigured as exc:
        raise ValidationError(str(exc), "canva_broker_url") from exc
    except CanvaNotConnected as exc:
        raise PrinterError(str(exc)) from exc
    except Exception as exc:
        logger.error("Canva export failed", design_id=design_id,
                     error=str(exc), exc_info=True)
        raise PrinterError(f"Canva export failed: {exc}") from exc

    stored_path = _save_export(image_bytes)
    logger.info("Canva design exported", design_id=design_id, path=stored_path)

    def job(path=stored_path, s=settings):
        printer_service.print_image(path, s)

    # params["type"] is "image", not "canva": it tells the UI how to re-open the
    # job, and an exported design IS an image job at that point. A new type would
    # mean teaching the queue UI about Canva for no behavioural gain. The
    # job_type tag stays "canva" so the queue still shows where it came from.
    # filename ends up as the download name when the job is re-opened into the
    # image composer, so give it a real .png extension rather than a bare title.
    params = {
        "type": "image",
        "filename": _export_filename(title),
        "settings": settings,
    }
    job_id = print_queue.submit(
        "canva", _short_label(title), job, params=params, file_path=stored_path
    )

    return {
        "success": True,
        "job_id": job_id,
        "message": "Print job queued",
    }


def export_canva_design(body: Dict[str, Any]) -> Dict[str, Any]:
    """Export a design WITHOUT printing it, for editing before printing.

    "Open in the composer" must not print. The queue's pause is global, so
    queueing-without-running is not available per job -- and a paused queue would
    still print the moment it resumed. So this exports and returns the PNG as a
    data URL, and the UI loads it into the image composer like any other file.

    No media guard here: nothing is printed, and refusing to *show* a design
    because the wrong roll is loaded would defeat the point of composing for a
    roll you are about to load.
    """
    design_id = (body or {}).get("design_id")
    if not design_id:
        raise ValidationError("design_id is required", "design_id")

    try:
        image_bytes = canva_service.export_design_png(design_id)
    except CanvaNotConfigured as exc:
        raise ValidationError(str(exc), "canva_broker_url") from exc
    except CanvaNotConnected as exc:
        raise PrinterError(str(exc)) from exc
    except Exception as exc:
        logger.error("Canva export failed", design_id=design_id,
                     error=str(exc), exc_info=True)
        raise PrinterError(f"Canva export failed: {exc}") from exc

    encoded = base64.b64encode(image_bytes).decode("ascii")
    return {
        "success": True,
        "filename": _export_filename((body or {}).get("title") or "canva-design"),
        "image": f"data:image/png;base64,{encoded}",
    }


def _save_export(image_bytes: bytes) -> str:
    """Write exported PNG bytes into uploads/jobs/ and return the path.

    Same folder as an uploaded image, because the queue's reprint/open and TTL
    cleanup work on whatever is in there -- a Canva design should be no different
    from a file the user dropped in.
    """
    jobs_folder = os.path.join(_get_upload_folder(), "jobs")
    os.makedirs(jobs_folder, exist_ok=True)
    path = os.path.join(jobs_folder, f"{uuid.uuid4().hex}.png")
    with open(path, "wb") as handle:
        handle.write(image_bytes)
    return path


def _get_upload_folder() -> str:
    """Resolve the upload folder the printer service is using."""
    return printer_service.upload_folder

def _export_filename(title: str) -> str:
    """A safe-ish .png filename from a design title, for re-open downloads."""
    from werkzeug.utils import secure_filename

    base = secure_filename(title or "") or "canva-design"
    if base.lower().endswith(".png"):
        return base
    return f"{base}.png"
