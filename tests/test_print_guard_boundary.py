"""
Tests for the media guard and, importantly, for *where* it is applied.

``describe_media_mismatch`` is exercised directly with synthetic status packets
so no printer is needed. The boundary tests assert a design decision that is
easy to undo by accident: the guard belongs on print endpoints only, never on
preview endpoints.
"""

import inspect

from src.services.printer_service import describe_media_mismatch


def _status(width, length=0):
    """A status dict shaped like read_usb_printer_status()'s output."""
    return {"media_width": width, "media_length": length}


# --------------------------------------------------------------------------
# What counts as a mismatch
# --------------------------------------------------------------------------

def test_matching_continuous_is_allowed():
    assert describe_media_mismatch("50", _status(50)) is None


def test_matching_die_cut_is_allowed():
    assert describe_media_mismatch("62x29", _status(62, 29)) is None


def test_wider_label_on_narrow_tape_is_rejected():
    msg = describe_media_mismatch("62", _status(50))
    assert msg and "62mm tape" in msg and "50mm loaded" in msg


def test_narrower_label_on_wide_tape_is_also_rejected():
    """Deliberately NOT allowed.

    A 29mm raster on 50mm tape looks like it should print with a margin, but
    convert() emits ``ESC i z`` carrying width_mm=29. A raster that declares a
    width the printer does not have is silently discarded by the QL -- the exact
    failure this guard exists to prevent -- so it is a mismatch like any other.
    """
    msg = describe_media_mismatch("29", _status(50))
    assert msg and "29mm tape" in msg and "50mm loaded" in msg


def test_continuous_label_on_die_cut_media_is_rejected():
    msg = describe_media_mismatch("62", _status(62, 29))
    assert msg and "continuous" in msg and "die-cut" in msg


def test_die_cut_label_on_continuous_media_is_rejected():
    msg = describe_media_mismatch("62x29", _status(62, 0))
    assert msg and "die-cut" in msg


def test_die_cut_length_difference_is_rejected():
    msg = describe_media_mismatch("62x100", _status(62, 29))
    assert msg and "62x100mm" in msg


def test_no_media_loaded_is_reported():
    msg = describe_media_mismatch("50", _status(0))
    assert msg and "no media detected" in msg


# --------------------------------------------------------------------------
# Inconclusive input must not invent a failure
# --------------------------------------------------------------------------

def test_unknown_label_identifier_is_allowed_through():
    """convert() reports a bad identifier more precisely than we can."""
    assert describe_media_mismatch("not-a-label", _status(50)) is None


def test_missing_status_is_allowed_through():
    assert describe_media_mismatch("50", None) is None


def test_missing_label_size_is_allowed_through():
    assert describe_media_mismatch(None, _status(50)) is None


# --------------------------------------------------------------------------
# WHERE the guard is applied -- the part that is easy to break later
# --------------------------------------------------------------------------

PRINT_CONTROLLERS = [
    ("src.api.text_controller", "print_text"),
    ("src.api.image_controller", "print_image"),
    ("src.api.qrcode_controller", "print_qr_code"),
    ("src.api.label_controller", "print_text_qrcode_label"),
    ("src.api.pdf_controller", "print_pdf"),
    ("src.api.text_image_controller", "print_text_image"),
]

PREVIEW_FUNCTIONS = [
    ("src.api.preview_controller", "preview_text"),
    ("src.api.preview_controller", "preview_qrcode"),
    ("src.api.preview_controller", "preview_label"),
    ("src.api.preview_controller", "preview_image"),
    ("src.api.pdf_controller", "preview_pdf"),
]


def _source_of(module_name, func_name):
    module = __import__(module_name, fromlist=[func_name])
    return inspect.getsource(getattr(module, func_name))


def test_every_print_endpoint_enforces_media_match():
    """A print path that skips the guard reintroduces the silent-discard bug."""
    missing = [
        f"{m}.{f}" for m, f in PRINT_CONTROLLERS
        if "enforce_media_match" not in _source_of(m, f)
    ]
    assert not missing, f"print endpoints missing the media guard: {missing}"


def test_no_preview_endpoint_enforces_media_match():
    """Previews must render for a roll that is not loaded yet.

    Composing a label for the roll you are about to put in is normal, and
    rendering touches no hardware. Guarding a preview would break that workflow
    and protect nothing.
    """
    guarded = [
        f"{m}.{f}" for m, f in PREVIEW_FUNCTIONS
        if "enforce_media_match" in _source_of(m, f)
    ]
    assert not guarded, (
        f"preview endpoints must not enforce media match: {guarded}")
