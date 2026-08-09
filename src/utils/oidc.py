"""OpenID Connect login, for putting the UI behind an identity provider.

The API-key hook next door authenticates *machines*. This authenticates a
*person*, because the UI itself has never been protected: the auth hook only
covers ``/api/v1/*`` and explicitly exempts ``/``, so anyone who could reach the
app could drive the printer and change its settings.

Opt-in exactly like ``API_KEY``: without ``OIDC_ENABLED`` nothing here runs and
the app behaves as it always has.

Why the app and not the edge
----------------------------
This exists so the UI can be embedded in a Home Assistant dashboard, i.e. in a
third-party iframe, and edge auth cannot do that. An ALB's OIDC session cookie
carries no ``SameSite`` attribute, so browsers treat it as ``Lax`` and withhold
it on framed cross-site requests -- the frame can never establish a session of
its own. There is no annotation to change that.

So the session cookie is ours, and it is set ``SameSite=None; Secure`` so it
survives framing.

Why there is no auto-redirect
-----------------------------
A framed app cannot send the user to Keycloak by itself: Keycloak defends its
login page with ``frame-ancestors 'self'`` and ``X-Frame-Options: SAMEORIGIN``,
so the login page cannot render in the frame at all. Relaxing that on the
identity provider is not worth doing.

Instead an unauthenticated request to the UI gets a small page with a sign-in
button that opens Keycloak in a real tab (``target="_blank"``). After that the
session cookie exists and the frame works normally. API requests get a 401
rather than the page, so machine callers see an error instead of HTML.
"""

import os
import secrets
import time
from urllib.parse import urlencode

import requests
import structlog
from flask import jsonify, redirect, request, session, url_for

logger = structlog.get_logger()

# How long a session is good for before the user signs in again.
SESSION_MAX_AGE_SECONDS = 12 * 60 * 60

# Session keys.
_SESSION_USER = "oidc_user"
_SESSION_EXPIRES = "oidc_expires_at"
_SESSION_STATE = "oidc_state"
_SESSION_NONCE = "oidc_nonce"


def oidc_enabled():
    """Whether OIDC login is enforced.

    Read from the environment on every call rather than cached at import, to
    match ``auth.get_expected_api_key`` and keep tests simple.
    """
    return _flag("OIDC_ENABLED")


def _flag(name):
    return str(os.environ.get(name, "")).strip().lower() in ("true", "yes", "1")


def _conf():
    """OIDC settings from the environment, or None when incomplete.

    Returning None rather than raising lets ``register_oidc`` refuse to start
    the feature and log why, instead of failing every request at runtime.
    """
    issuer = (os.environ.get("OIDC_ISSUER_URL") or "").strip().rstrip("/")
    client_id = (os.environ.get("OIDC_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("OIDC_CLIENT_SECRET") or "").strip()
    if not (issuer and client_id and client_secret):
        return None
    return {
        "issuer": issuer,
        "client_id": client_id,
        "client_secret": client_secret,
        # Keycloak's standard endpoints. Discovery would be tidier but adds a
        # network call at startup that fails the whole app when the IdP is
        # briefly down; these paths are stable for Keycloak realms.
        "auth_endpoint": f"{issuer}/protocol/openid-connect/auth",
        "token_endpoint": f"{issuer}/protocol/openid-connect/token",
        "logout_endpoint": f"{issuer}/protocol/openid-connect/logout",
    }


def _redirect_uri():
    """Absolute callback URL.

    Built from OIDC_REDIRECT_BASE when set, because behind a proxy the app sees
    its own scheme and host rather than the public one, and a redirect_uri that
    does not match what Keycloak has registered fails as
    ``Invalid parameter: redirect_uri``.
    """
    base = (os.environ.get("OIDC_REDIRECT_BASE") or "").strip().rstrip("/")
    if base:
        return f"{base}/auth/callback"
    return url_for("oidc_callback", _external=True)


def current_user():
    """The signed-in user's name, or None.

    Also enforces expiry: a session past its lifetime is treated as absent and
    cleared, so a stale cookie cannot keep access alive indefinitely.
    """
    user = session.get(_SESSION_USER)
    if not user:
        return None
    expires_at = session.get(_SESSION_EXPIRES, 0)
    if not expires_at or time.time() > expires_at:
        _clear_session()
        return None
    return user


def _clear_session():
    for key in (_SESSION_USER, _SESSION_EXPIRES, _SESSION_STATE, _SESSION_NONCE):
        session.pop(key, None)


def register_oidc(app):
    """Add the login routes and the session gate.

    Does nothing unless OIDC_ENABLED is set, so this is inert by default and
    every existing deployment is unaffected.
    """
    if not oidc_enabled():
        return False

    conf = _conf()
    if conf is None:
        # Loud, because the intent was clearly to protect the app and it is not
        # protected. Deliberately not fatal: refusing to boot would take the
        # printer offline over a config typo.
        logger.error(
            "OIDC_ENABLED is set but OIDC_ISSUER_URL / OIDC_CLIENT_ID / "
            "OIDC_CLIENT_SECRET are not all present - UI is NOT protected")
        return False

    # The session cookie has to survive being sent from inside an iframe, which
    # means SameSite=None, which browsers only accept together with Secure.
    app.config.update(
        SESSION_COOKIE_SAMESITE="None",
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
    )

    _register_routes(app, conf)
    _register_gate(app)

    logger.info("OIDC login enabled", issuer=conf["issuer"],
                client_id=conf["client_id"])
    return True


def _register_routes(app, conf):
    @app.route("/auth/login")
    def oidc_login():
        # state and nonce are the CSRF and replay defences for the code flow.
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        session[_SESSION_STATE] = state
        session[_SESSION_NONCE] = nonce

        params = {
            "client_id": conf["client_id"],
            "response_type": "code",
            "scope": "openid profile email",
            "redirect_uri": _redirect_uri(),
            "state": state,
            "nonce": nonce,
        }
        return redirect(f"{conf['auth_endpoint']}?{urlencode(params)}")

    @app.route("/auth/callback")
    def oidc_callback():
        error = request.args.get("error")
        if error:
            logger.warning("OIDC callback returned an error", error=error,
                           description=request.args.get("error_description"))
            return _signin_page("Sign-in failed. Try again."), 401

        expected_state = session.pop(_SESSION_STATE, None)
        if not expected_state or request.args.get("state") != expected_state:
            # Either a forged callback or a session that vanished mid-flow.
            logger.warning("OIDC state mismatch")
            return _signin_page("Sign-in expired. Try again."), 401

        code = request.args.get("code")
        if not code:
            return _signin_page("Sign-in failed. Try again."), 401

        try:
            token = requests.post(
                conf["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": _redirect_uri(),
                    "client_id": conf["client_id"],
                    "client_secret": conf["client_secret"],
                },
                timeout=15,
            )
            token.raise_for_status()
            payload = token.json()
        except requests.RequestException as e:
            logger.error("OIDC token exchange failed", error=str(e))
            return _signin_page("Could not reach the sign-in service."), 502

        claims = _claims_from_id_token(payload.get("id_token"))
        if claims is None:
            logger.error("OIDC token response had no usable id_token")
            return _signin_page("Sign-in failed. Try again."), 401

        expected_nonce = session.pop(_SESSION_NONCE, None)
        if expected_nonce and claims.get("nonce") != expected_nonce:
            logger.warning("OIDC nonce mismatch")
            return _signin_page("Sign-in expired. Try again."), 401

        user = (claims.get("preferred_username") or claims.get("email")
                or claims.get("sub") or "user")
        session[_SESSION_USER] = user
        session[_SESSION_EXPIRES] = time.time() + SESSION_MAX_AGE_SECONDS
        session.permanent = False
        logger.info("OIDC sign-in", user=user)

        # Opened in a new tab from the frame's sign-in button, so there is
        # nothing useful to go back to: tell the user to return to the frame.
        return _signed_in_page(user)

    @app.route("/auth/logout")
    def oidc_logout():
        user = session.get(_SESSION_USER)
        _clear_session()
        logger.info("OIDC sign-out", user=user)
        return _signin_page("Signed out.")

    @app.route("/auth/whoami")
    def oidc_whoami():
        user = current_user()
        return jsonify({"authenticated": bool(user), "user": user})


def _claims_from_id_token(id_token):
    """Read the claims out of an id_token.

    The token comes straight from the IdP's token endpoint over TLS, in a
    request authenticated with the client secret, so this decodes rather than
    verifies the signature -- there is no untrusted path for it to arrive by.
    The nonce is still checked by the caller, which is what defeats replay.
    """
    if not id_token:
        return None
    try:
        import base64
        import json

        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, IndexError, TypeError) as e:
        logger.error("Could not decode id_token", error=str(e))
        return None


# Paths that must stay reachable without a session, or sign-in could never
# happen and the health probes would start failing.
_OIDC_EXEMPT_PREFIXES = ("/auth/", "/css/", "/js/", "/static/")
_OIDC_EXEMPT_EXACT = ("/health", "/health/printer")


def _register_gate(app):
    """Require a session for everything that is not exempt."""
    from src.utils.auth import API_KEY_HEADER, auth_enabled, is_valid_api_key

    @app.before_request
    def require_oidc_session():
        path = request.path

        if path in _OIDC_EXEMPT_EXACT:
            return None
        if any(path.startswith(p) for p in _OIDC_EXEMPT_PREFIXES):
            return None

        # A valid API key is still a valid identity. Home Assistant, Homebox and
        # the Canva poller call this API with a key and cannot do a browser
        # login; gating them behind OIDC would break every one of them.
        if auth_enabled() and is_valid_api_key(request.headers.get(API_KEY_HEADER)):
            return None

        if current_user():
            return None

        # Machine callers get a machine answer; browsers get somewhere to click.
        if path.startswith("/api/"):
            return jsonify({"error": "unauthorized", "login": "/auth/login"}), 401
        return _signin_page(), 401


def _page(body):
    return (
        "<!doctype html><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Label printer</title>"
        "<style>"
        "body{font-family:system-ui,sans-serif;background:#111;color:#eee;"
        "display:flex;min-height:100vh;align-items:center;justify-content:center;"
        "margin:0;text-align:center}"
        ".c{padding:2rem}"
        "a.btn{display:inline-block;background:#3b82f6;color:#fff;padding:.7rem 1.4rem;"
        "border-radius:.5rem;text-decoration:none;font-weight:600}"
        "p{color:#9ca3af;font-size:.9rem;line-height:1.5}"
        "</style>"
        f"<div class=c>{body}</div>"
    )


def _signin_page(message=None):
    note = f"<p>{message}</p>" if message else ""
    # target=_blank is load-bearing: Keycloak's login page sends
    # frame-ancestors 'self', so it cannot render inside the dashboard frame.
    return _page(
        "<h2>Label printer</h2>"
        f"{note}"
        "<p><a class=btn href='/auth/login' target='_blank' rel='noopener'>"
        "Sign in with Keycloak</a></p>"
        "<p>Opens in a new tab. Come back here and refresh once signed in.</p>"
    )


def _signed_in_page(user):
    return _page(
        "<h2>Signed in</h2>"
        f"<p>as {user}</p>"
        "<p>You can close this tab and refresh the label printer.</p>"
    )
