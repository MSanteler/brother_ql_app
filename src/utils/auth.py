"""API-Key authentication helpers.

Authentication is *opt-in*: it is only enforced when the ``API_KEY`` environment
variable is set. Enforcement itself lives in a Flask ``before_request`` hook in
``app.py`` (see the ``enforce_api_key`` handler) rather than in connexion
security schemes, which would be too invasive and would also gate the bundled
UI and the health probes.
"""

import os
import hmac

# Header carrying the client-supplied API key.
API_KEY_HEADER = "X-API-Key"

# Header the Home Assistant ingress proxy adds to every request it forwards.
INGRESS_PATH_HEADER = "X-Ingress-Path"


def get_expected_api_key():
    """Return the configured API key, or ``None`` when auth is disabled.

    Reads ``os.environ`` on every call so that the value is never cached at
    import time (relevant for tests and for WSGI workers spawned after the
    environment was prepared).
    """
    key = os.environ.get("API_KEY")
    if key is None:
        return None
    # Treat an empty / whitespace-only value as "not set" so an accidentally
    # blank env var does not silently disable every request.
    key = key.strip()
    return key or None


def auth_enabled():
    """Whether API-key authentication is enforced."""
    return get_expected_api_key() is not None


def is_valid_api_key(provided):
    """Constant-time comparison of a provided key against the expected one.

    Returns ``False`` when auth is disabled or when ``provided`` is missing.
    """
    expected = get_expected_api_key()
    if expected is None or not provided:
        return False
    return hmac.compare_digest(str(provided), expected)


def apikey_info(apikey, required_scopes=None):
    """connexion ``x-apikeyInfoFunc`` callback.

    Only wired into the OpenAPI spec when API_KEY is set (see ``create_app``),
    so connexion enforces the X-API-Key header on the documented operations and
    the Swagger UI "Authorize" button works. Returns an identity dict for a
    valid key, or ``None`` (→ connexion 401) for an invalid one.
    """
    if is_valid_api_key(apikey):
        return {"sub": "apikey"}
    return None


def get_ingress_proxy():
    """The address of a trusted ingress proxy, or ``None`` when unset.

    Set ``INGRESS_PROXY`` when the app runs as a Home Assistant add-on with
    ingress. HA authenticates the person before proxying, and its Supervisor
    always connects from one fixed address (172.30.32.2), so a request arriving
    from there has already been signed in -- asking again would mean a second
    login inside a panel that cannot show one.
    """
    proxy = (os.environ.get("INGRESS_PROXY") or "").strip()
    return proxy or None


def ingress_enabled():
    """Whether requests from the ingress proxy are trusted."""
    return get_ingress_proxy() is not None


def is_ingress_request():
    """Whether the current request came through the trusted ingress proxy.

    Decided by the TCP peer address alone. The ``X-Ingress-Path`` header the
    proxy adds is NOT evidence: any client can send it, while only the proxy
    can connect from its own address. Nothing in front of the app rewrites
    ``remote_addr`` unless OIDC is on, and OIDC's ProxyFix trusts only the
    forwarded scheme, never the forwarded address.
    """
    proxy = get_ingress_proxy()
    if proxy is None:
        return False
    from flask import request

    return request.remote_addr == proxy


def ingress_prefix():
    """The path the ingress proxy serves this app under, or "".

    Only for building redirects: a Location of ``/`` would leave the panel and
    land on Home Assistant's own front page.
    """
    if not is_ingress_request():
        return ""
    from flask import request

    return (request.headers.get(INGRESS_PATH_HEADER) or "").rstrip("/")


def ingress_info(value, required_scopes=None):
    """connexion callback for the IngressAuth scheme.

    The scheme names the ``X-Ingress-Path`` header so connexion has something to
    look for, but the header's value is not the credential -- the peer address
    is (see ``is_ingress_request``).
    """
    if is_ingress_request():
        return {"sub": "ingress"}
    return None


def session_info(cookie, required_scopes=None):
    """connexion callback for the SessionAuth scheme.

    A signed-in browser is an identity too, and connexion has to be told so:
    it enforces the declared `security` before any before_request hook runs, so
    with only ApiKeyAuth declared the bundled UI could not call its own API --
    every request was rejected with "No auth provided" while the page itself
    loaded fine.

    The cookie value is not inspected here. Flask has already verified its
    signature by the time this runs, and ``current_user`` re-reads the session
    and enforces expiry; trusting the raw cookie text instead would be a way
    in for anything cookie-shaped.
    """
    from src.utils.oidc import current_user, oidc_enabled

    if oidc_enabled() and current_user():
        return {"sub": "session"}
    return None
