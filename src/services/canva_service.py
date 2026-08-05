"""
Browse and export Canva designs, so a design can be printed without leaving the
app.

**This module holds no OAuth credentials.** Canva's refresh tokens are
single-use and rotate on every refresh, so exactly one process may own them --
two owners race and the integration dies with a "refresh failed" that looks like
a Canva outage. That owner is the broker service (``canva_broker_url``), which
also keeps the client secret off this machine. Here we only ask it for a
short-lived access token and use that directly against Canva's API.

What this means in practice:

* Nothing durable is stored. No local design copies, no watched folders, no
  import state, no sync cursor. A design is listed on demand and exported only
  when it is actually going to be printed.
* Listing is free. Canva returns a thumbnail URL with folder items, so browsing
  costs no export quota; only an actual print spends one.
"""

import time
from typing import Any, Dict, List, Optional

import requests
import structlog

logger = structlog.get_logger()

CANVA_API = "https://api.canva.com/rest/v1"

# Exports are asynchronous. Poll this long before giving up -- a complex design
# can take a while, and failing early would look like a broken integration.
EXPORT_POLL_ATTEMPTS = 60
EXPORT_POLL_INTERVAL = 2.0

# Network timeout for both the broker and Canva itself.
HTTP_TIMEOUT = 30


class CanvaNotConfigured(RuntimeError):
    """No broker URL is set, so Canva browsing is switched off."""


class CanvaNotConnected(RuntimeError):
    """The broker has no usable Canva authorization yet."""


class CanvaService:
    """Thin Canva client that borrows its access token from the broker."""

    def __init__(self) -> None:
        # Cached access token: (token, expires_at). Canva's tokens last ~4h, so
        # caching avoids a broker round trip per request, but the broker remains
        # the source of truth -- we never refresh anything ourselves.
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0

    # -- configuration ----------------------------------------------------
    def _config(self) -> Dict[str, Any]:
        from src.services.settings_service import settings_service

        settings = settings_service.get_settings()
        url = (settings.get("canva_broker_url") or "").strip().rstrip("/")
        if not url:
            raise CanvaNotConfigured(
                "Canva browsing is not configured. Set canva_broker_url in "
                "Settings to the label-library service that holds the Canva "
                "authorization.")
        return {
            "url": url,
            "token": (settings.get("canva_broker_token") or "").strip(),
        }

    def is_configured(self) -> bool:
        """Whether a broker URL is set, without raising."""
        try:
            self._config()
            return True
        except CanvaNotConfigured:
            return False

    # -- access token -----------------------------------------------------
    def _access_token(self, force: bool = False) -> str:
        """Return a usable Canva access token, asking the broker if needed.

        Args:
            force: Ignore the cached token. Used after a 401, in case the cache
                is holding a token Canva has already rejected.
        """
        if not force and self._token and time.time() < self._token_expires_at:
            return self._token

        config = self._config()
        headers = {}
        if config["token"]:
            headers["X-Access-Token"] = config["token"]

        try:
            response = requests.get(
                f"{config['url']}/api/canva-token",
                headers=headers,
                timeout=HTTP_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise CanvaNotConnected(
                f"Could not reach the Canva token service: {exc}") from exc

        if response.status_code == 503:
            # The broker is up but has no authorization -- a different problem
            # from being unreachable, and it needs a human, so say which.
            body = _safe_json(response)
            raise CanvaNotConnected(
                body.get("message")
                or "Canva is not connected. Authorize the label-library service.")
        if response.status_code in (401, 403):
            raise CanvaNotConnected(
                "The Canva token service rejected our credentials. Check "
                "canva_broker_token in Settings.")
        if response.status_code != 200:
            raise CanvaNotConnected(
                f"Canva token service returned {response.status_code}")

        body = _safe_json(response)
        token = body.get("access_token")
        if not token:
            raise CanvaNotConnected("Canva token service returned no token")

        self._token = token
        # Trust the broker's absolute expiry, minus a minute of slack. Fall back
        # to a short cache if it did not send one, rather than caching forever.
        expires_at = body.get("expires_at")
        self._token_expires_at = (
            float(expires_at) - 60 if expires_at else time.time() + 300)
        return token

    def _get(self, path: str, **kwargs) -> Dict[str, Any]:
        """GET from Canva, retrying once with a fresh token on 401."""
        for attempt in (0, 1):
            token = self._access_token(force=bool(attempt))
            response = requests.get(
                f"{CANVA_API}{path}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=HTTP_TIMEOUT,
                **kwargs,
            )
            if response.status_code == 401 and attempt == 0:
                # Cached token was stale or revoked; ask the broker again.
                continue
            response.raise_for_status()
            return response.json()
        raise CanvaNotConnected("Canva rejected the access token twice")

    # -- browsing ---------------------------------------------------------
    def list_folder(self, folder_id: str) -> List[Dict[str, Any]]:
        """List the designs in a folder.

        Costs no export quota: Canva includes a thumbnail URL with each item, so
        the UI can show the designs without exporting any of them.

        Thumbnail URLs are short-lived (~15 minutes), which is exactly why they
        are passed straight through and never cached.
        """
        designs: List[Dict[str, Any]] = []
        continuation = None

        while True:
            params: Dict[str, Any] = {"item_types": "design", "limit": 100}
            if continuation:
                params["continuation"] = continuation

            body = self._get(f"/folders/{folder_id}/items", params=params)

            for item in body.get("items", []):
                if item.get("type") != "design":
                    continue
                design = item.get("design") or {}
                if not design.get("id"):
                    continue
                designs.append({
                    "id": design["id"],
                    "title": design.get("title") or "Untitled",
                    "updated_at": design.get("updated_at")
                    or design.get("created_at") or 0,
                    "thumbnail": (design.get("thumbnail") or {}).get("url"),
                    "edit_url": (design.get("urls") or {}).get("edit_url"),
                })

            continuation = body.get("continuation")
            if not continuation:
                break

        logger.info("Listed Canva folder", folder_id=folder_id,
                    designs=len(designs))
        return designs

    def list_folders(self, parent: str = "root") -> List[Dict[str, Any]]:
        """List sub-folders of a folder, so the UI can offer a picker."""
        folders: List[Dict[str, Any]] = []
        continuation = None

        while True:
            params: Dict[str, Any] = {"item_types": "folder", "limit": 100}
            if continuation:
                params["continuation"] = continuation

            body = self._get(f"/folders/{parent}/items", params=params)

            for item in body.get("items", []):
                folder = item.get("folder") or {}
                if folder.get("id"):
                    folders.append({
                        "id": folder["id"],
                        "name": folder.get("name") or "Untitled folder",
                    })

            continuation = body.get("continuation")
            if not continuation:
                break

        return folders

    # -- export -----------------------------------------------------------
    def export_design_png(self, design_id: str) -> bytes:
        """Export a design as PNG and return the image bytes.

        This is the call that costs export quota, so it happens only when a
        design is actually being printed -- never while browsing.
        """
        token = self._access_token()
        response = requests.post(
            f"{CANVA_API}/exports",
            headers={"Authorization": f"Bearer {token}"},
            json={"design_id": design_id, "format": {"type": "png"}},
            timeout=HTTP_TIMEOUT,
        )
        response.raise_for_status()
        job_id = response.json()["job"]["id"]

        for _ in range(EXPORT_POLL_ATTEMPTS):
            time.sleep(EXPORT_POLL_INTERVAL)
            poll = requests.get(
                f"{CANVA_API}/exports/{job_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=HTTP_TIMEOUT,
            )
            poll.raise_for_status()
            job = poll.json()["job"]
            status = job.get("status")

            if status == "success":
                urls = job.get("urls") or []
                if not urls:
                    raise RuntimeError("Canva reported success but sent no URL")
                # The download URL is pre-signed, so no Authorization header --
                # sending one can make the CDN reject the request.
                image = requests.get(urls[0], timeout=HTTP_TIMEOUT)
                image.raise_for_status()
                logger.info("Exported Canva design", design_id=design_id,
                            bytes=len(image.content))
                return image.content

            if status == "failed":
                raise RuntimeError(
                    f"Canva export failed: {job.get('error') or 'unknown error'}")

        raise TimeoutError("Canva export did not finish in time")


def _safe_json(response) -> Dict[str, Any]:
    """Parse a JSON body, tolerating a non-JSON error page."""
    try:
        return response.json() or {}
    except ValueError:
        return {}


canva_service = CanvaService()
