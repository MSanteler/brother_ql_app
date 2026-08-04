"""
Shared helpers that reject a print request before it reaches the queue.

Two guards live here:

* Large batches need an explicit confirmation flag, so a slip of the keyboard
  cannot drive the printer through dozens of labels.
* A label size that does not match the media actually loaded is rejected up
  front. The printer silently discards such a job -- no error, no paper -- and
  because printing is asynchronous the failure would otherwise only appear in
  the queue's job record, long after the caller was told "queued".

Both raise before anything is enqueued, so the caller learns immediately.
"""

import structlog

from src.utils.exceptions import ConfirmationRequiredError, ValidationError

logger = structlog.get_logger()

# Copy count at/above which a print request needs explicit confirmation.
LARGE_BATCH_THRESHOLD = 10


def is_confirmed(value) -> bool:
    """Interpret a confirmation flag from JSON or form data as a boolean.

    Accepts native booleans as well as the common truthy string spellings
    ("true", "yes", "1"), case-insensitively. Everything else is treated as
    not confirmed.

    Args:
        value: The raw confirmation value from the request body/form.

    Returns:
        True when the value expresses confirmation, False otherwise.
    """
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "yes", "1")


def enforce_large_batch_confirmation(copies, confirmed: bool) -> None:
    """Reject large batches that are not explicitly confirmed.

    Args:
        copies: Requested number of copies (parsed leniently, defaulting to 1).
        confirmed: Whether the request carries a valid confirmation flag.

    Raises:
        ConfirmationRequiredError: When ``copies`` is at/above
            ``LARGE_BATCH_THRESHOLD`` and ``confirmed`` is falsey.
    """
    try:
        n = int(copies or 1)
    except (TypeError, ValueError):
        n = 1
    if n >= LARGE_BATCH_THRESHOLD and not confirmed:
        raise ConfirmationRequiredError(n, LARGE_BATCH_THRESHOLD)


def enforce_media_match(settings) -> None:
    """Reject a print whose label size cannot print on the loaded media.

    Printing is queued and executed on a worker, so an error raised during the
    print itself never reaches the caller -- the API has already answered
    ``{"success": true, "job_id": ...}``. Checking here means a mismatch is
    reported in the response instead of being buried in the job record.

    **Call this from print endpoints only.** The ``/preview`` endpoints
    deliberately do not use it: composing a label for a roll you are about to
    load is a normal thing to do, and rendering touches no hardware, so there is
    nothing to protect. A mismatch only matters at the moment paper should move.
    Adding this to a preview would break that workflow for no safety gain.

    There is no override flag, and adding one would be a mistake. It is tempting
    to think a *narrower* continuous label on wider tape is harmless -- a 554px
    raster on 696px tape, just with a margin -- but the raster's own ``ESC i z``
    media command carries the narrow width (verified: label "29" emits
    ``width_mm=29``). Declaring a width the printer does not have is exactly the
    condition that makes the QL silently discard the job, which is the bug this
    guard exists to prevent. Every mismatch class is either a silent discard or
    a ``convert()`` rejection; none of them print.

    Only applies to USB printers, which can be asked what media they hold.
    Network printers report through IPP and are left alone. Anything
    inconclusive (printer asleep, status unreadable, unknown label) is allowed
    through: this guard exists to catch a definite mismatch, not to add a new
    way for printing to fail.

    Args:
        settings: Resolved print settings, containing printer_uri and
            label_size.

    Raises:
        ValidationError: When the loaded media definitely cannot print the
            requested label size.
    """
    if not settings:
        return

    # Imported here rather than at module scope: printer_service pulls in the
    # USB stack, and this module is imported by every print controller.
    from src.services.printer_service import (
        describe_media_mismatch,
        read_usb_printer_status,
    )

    status = read_usb_printer_status(settings.get("printer_uri"))
    if not status:
        # Asleep, unreadable, or not a USB printer -- nothing conclusive.
        return

    mismatch = describe_media_mismatch(settings.get("label_size"), status)
    if mismatch:
        logger.warning("Rejected print: media mismatch",
                       label_size=settings.get("label_size"),
                       media_width_mm=status.get("media_width"),
                       media_length_mm=status.get("media_length"))
        raise ValidationError(
            f"{mismatch}. Load the matching roll, or choose a label size that "
            f"fits the media already in the printer.",
            "label_size")
