"""
Shared pytest configuration and lightweight dependency stubs.

The application code imports a handful of heavy / hardware-oriented third-party
packages at *module import time* (``structlog``, ``brother_ql``, ``PIL``,
``qrcode``, ``pysnmp``). In the CI/Docker image all of those are installed and
the real packages are used. To keep the unit tests robust and runnable even in
a bare environment, this conftest installs *minimal* stand-in modules for any of
them that happen to be missing.

Important properties of these stubs:

* They are only registered when the real package cannot be imported, so when the
  full dependency set is present (Docker) the genuine packages are used and the
  stubs are never touched.
* ``brother_ql.backends.guess_backend`` is given a small, deterministic
  implementation (tcp:// -> "network", usb:// -> "pyusb", everything else ->
  "linux_kernel"). Tests that care about backend behaviour additionally
  ``patch`` ``guess_backend`` explicitly, so they behave identically whether the
  real brother_ql is installed or not.

Both the repo root and ``src`` are added to ``sys.path`` to match the project's
``pyproject.toml`` ``pythonpath = [".", "src"]`` configuration, so the tests can
be collected even when pytest is invoked from an unusual working directory.
"""

import os
import sys
import types

# --- Make `src` and the repo root importable (mirrors pyproject pythonpath) ---
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_REPO_ROOT, os.path.join(_REPO_ROOT, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _ensure_structlog() -> None:
    try:
        import structlog  # noqa: F401
        return
    except ImportError:
        pass

    class _NoopLogger:
        def __getattr__(self, _name):
            def _noop(*_args, **_kwargs):
                return None
            return _noop

    stub = types.ModuleType("structlog")
    stub.get_logger = lambda *a, **k: _NoopLogger()  # type: ignore[attr-defined]
    sys.modules["structlog"] = stub


def _ensure_brother_ql() -> None:
    try:
        import brother_ql.backends  # noqa: F401
        return
    except ImportError:
        pass

    brother_ql = types.ModuleType("brother_ql")
    backends = types.ModuleType("brother_ql.backends")

    def guess_backend(uri: str) -> str:
        uri = (uri or "").lower()
        if uri.startswith("tcp://"):
            return "network"
        if uri.startswith("usb://"):
            return "pyusb"
        if uri.startswith("file://"):
            return "linux_kernel"
        return "linux_kernel"

    def backend_factory(_backend_type):  # pragma: no cover - not exercised here
        raise RuntimeError("backend_factory stub should be patched in tests")

    backends.guess_backend = guess_backend  # type: ignore[attr-defined]
    backends.backend_factory = backend_factory  # type: ignore[attr-defined]

    # Sub-modules referenced by printer_service at import time.
    raster = types.ModuleType("brother_ql.raster")
    raster.BrotherQLRaster = object  # type: ignore[attr-defined]
    conversion = types.ModuleType("brother_ql.conversion")
    conversion.convert = lambda *a, **k: b""  # type: ignore[attr-defined]
    # printer_service imports interpret_response at module scope for the
    # ESC i S status read, so without this stub the module cannot be imported
    # at all in a bare environment and every test in it fails to collect.
    reader = types.ModuleType("brother_ql.reader")
    reader.interpret_response = lambda *a, **k: {}  # type: ignore[attr-defined]

    # describe_media_mismatch looks up tape geometry in ALL_LABELS and returns
    # None if the import fails -- i.e. without this stub the media-guard tests
    # silently assert against a disabled guard and every rejection case "passes"
    # by returning None. The identifiers below are the rolls this printer
    # actually uses; tape_size is (width_mm, length_mm), 0 length = continuous.
    labels = types.ModuleType("brother_ql.labels")

    class _Label:
        def __init__(self, identifier, tape_size):
            self.identifier = identifier
            self.tape_size = tape_size

    labels.ALL_LABELS = [  # type: ignore[attr-defined]
        _Label("12", (12, 0)),
        _Label("29", (29, 0)),
        _Label("50", (50, 0)),
        _Label("62", (62, 0)),
        _Label("29x62", (29, 62)),
        _Label("29x90", (29, 90)),
        _Label("62x29", (62, 29)),
        _Label("62x100", (62, 100)),
    ]

    brother_ql.backends = backends  # type: ignore[attr-defined]
    brother_ql.raster = raster  # type: ignore[attr-defined]
    brother_ql.conversion = conversion  # type: ignore[attr-defined]
    brother_ql.reader = reader  # type: ignore[attr-defined]
    brother_ql.labels = labels  # type: ignore[attr-defined]

    sys.modules["brother_ql"] = brother_ql
    sys.modules["brother_ql.backends"] = backends
    sys.modules["brother_ql.raster"] = raster
    sys.modules["brother_ql.conversion"] = conversion
    sys.modules["brother_ql.reader"] = reader
    sys.modules["brother_ql.labels"] = labels


def _ensure_qrcode() -> None:
    try:
        import qrcode  # noqa: F401
        return
    except ImportError:
        pass

    qrcode = types.ModuleType("qrcode")
    constants = types.ModuleType("qrcode.constants")
    for _i, _name in enumerate(
        ("ERROR_CORRECT_L", "ERROR_CORRECT_M", "ERROR_CORRECT_Q", "ERROR_CORRECT_H")
    ):
        setattr(constants, _name, _i)
    qrcode.constants = constants  # type: ignore[attr-defined]
    qrcode.QRCode = object  # type: ignore[attr-defined]
    sys.modules["qrcode"] = qrcode
    sys.modules["qrcode.constants"] = constants


def _ensure_pil() -> None:
    try:
        import PIL  # noqa: F401
        return
    except ImportError:
        pass

    pil = types.ModuleType("PIL")
    image_mod = types.ModuleType("PIL.Image")
    for _name in ("Image", "ImageDraw", "ImageFont", "ImageOps"):
        sub = types.ModuleType(f"PIL.{_name}")
        setattr(pil, _name, sub)
        sys.modules[f"PIL.{_name}"] = sub
    # printer_service does `from PIL import Image, ...`
    sys.modules["PIL"] = pil
    sys.modules["PIL.Image"] = image_mod
    pil.Image = image_mod  # type: ignore[attr-defined]


# Register stubs (no-ops when the real packages are installed).
_ensure_structlog()
_ensure_brother_ql()
_ensure_qrcode()
_ensure_pil()
