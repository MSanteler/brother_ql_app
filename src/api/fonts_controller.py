"""
Controller for font catalog API endpoints.

Two endpoints: one listing the families available for rendering, and one
serving a font file so the browser's live preview can use the same typeface the
printer will. Without the second, the preview renders in whatever the browser
picks and quietly disagrees with the print.
"""

import os

import structlog
from typing import Any, Dict, List
from flask import send_file

from src.services.font_service import font_service, STYLES
from src.utils.exceptions import ResourceNotFoundError, ConfigurationError

logger = structlog.get_logger()

# MIME types for the extensions the catalog accepts. mimetypes.guess_type is
# unreliable for fonts across platforms -- it returns None for .otf on some
# systems -- and a wrong type stops the browser using the face.
_FONT_MIMETYPES = {
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".ttc": "font/collection",
}


def list_fonts() -> Dict[str, Any]:
    """
    List the font families available for rendering.

    Returns:
        Dict with the family list, the default family, and the drop-in
        directory (so the UI can tell the user where to put their own fonts).
    """
    try:
        logger.info("Listing available fonts")
        families: List[Dict[str, Any]] = font_service.list_families()
        return {
            "fonts": families,
            "default_family": font_service.default_family(),
            "styles": list(STYLES),
            "user_font_dir": font_service.user_font_dir,
        }
    except Exception as e:
        logger.error("Error listing fonts", error=str(e), exc_info=True)
        raise ConfigurationError(f"Error listing fonts: {str(e)}")


def get_font_file(font_id: str):
    """
    Serve one font file, for the browser-side preview.

    ``font_id`` is ``"<family>:<style>"``. It is resolved through the catalog,
    which only ever yields paths it discovered itself -- a crafted id cannot
    escape into an arbitrary file read.

    Args:
        font_id: Identifier of the form ``Liberation Sans:bold``.

    Returns:
        The font file.

    Raises:
        ResourceNotFoundError: If no catalogued face matches.
    """
    try:
        path = font_service.path_for_id(font_id)
        if not path or not os.path.isfile(path):
            logger.warning("Font not found", font_id=font_id)
            raise ResourceNotFoundError(f"Font not found: {font_id}", "font")

        mimetype = _FONT_MIMETYPES.get(
            os.path.splitext(path)[1].lower(), "application/octet-stream"
        )
        logger.info("Serving font file", font_id=font_id, path=path)
        response = send_file(path, mimetype=mimetype)
        # Font files are immutable for the life of a container, and the preview
        # refetches on every font change; without this each switch is a round
        # trip. Kept private so a shared cache never serves a drop-in font
        # belonging to a different install.
        response.headers["Cache-Control"] = "private, max-age=86400"
        return response
    except ResourceNotFoundError:
        raise
    except Exception as e:
        logger.error("Error serving font file", font_id=font_id,
                     error=str(e), exc_info=True)
        raise ConfigurationError(f"Error serving font file: {str(e)}")
