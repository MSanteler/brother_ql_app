"""Tests for OIDC login.

The security-relevant assertions here are the ones about what stays reachable
without a session, and about API keys still working: gating the machine callers
(Home Assistant, Homebox, the Canva poller) behind a browser login would break
every one of them, and none can do an interactive sign-in.
"""

import base64
import json
import os
import time

import pytest

from src.utils import oidc


@pytest.fixture
def app_ctx():
    """A request context, so flask.session is usable.

    A bare Flask app rather than the real one: these tests are about session
    handling, and building the whole connexion app would drag in the printer
    stack for no benefit.
    """
    from flask import Flask

    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test-only"
    with app.test_request_context("/"):
        yield app


@pytest.fixture(autouse=True)
def clean_env():
    """Each test starts with no OIDC configuration."""
    keys = ("OIDC_ENABLED", "OIDC_ISSUER_URL", "OIDC_CLIENT_ID",
            "OIDC_CLIENT_SECRET", "OIDC_REDIRECT_BASE")
    saved = {k: os.environ.pop(k, None) for k in keys}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _configure():
    os.environ["OIDC_ENABLED"] = "true"
    os.environ["OIDC_ISSUER_URL"] = "https://auth.example.com/realms/r"
    os.environ["OIDC_CLIENT_ID"] = "label-printer"
    os.environ["OIDC_CLIENT_SECRET"] = "shh"


# --------------------------------------------------------------------------
# Opt-in, exactly like API_KEY
# --------------------------------------------------------------------------

def test_disabled_by_default():
    assert oidc.oidc_enabled() is False


def test_enabled_by_flag():
    _configure()
    assert oidc.oidc_enabled() is True


@pytest.mark.parametrize("value", ["true", "TRUE", "yes", "1"])
def test_flag_spellings(value):
    os.environ["OIDC_ENABLED"] = value
    assert oidc.oidc_enabled() is True


@pytest.mark.parametrize("value", ["false", "no", "0", "", "  "])
def test_falsey_spellings(value):
    os.environ["OIDC_ENABLED"] = value
    assert oidc.oidc_enabled() is False


def test_config_incomplete_returns_none():
    """A half-configured client must not look configured."""
    os.environ["OIDC_ENABLED"] = "true"
    os.environ["OIDC_ISSUER_URL"] = "https://auth.example.com/realms/r"
    # no client id/secret
    assert oidc._conf() is None


def test_config_complete():
    _configure()
    conf = oidc._conf()
    assert conf["client_id"] == "label-printer"
    assert conf["auth_endpoint"].endswith("/protocol/openid-connect/auth")
    assert conf["token_endpoint"].endswith("/protocol/openid-connect/token")


def test_trailing_slash_on_issuer_does_not_double_up():
    _configure()
    os.environ["OIDC_ISSUER_URL"] = "https://auth.example.com/realms/r/"
    assert "//protocol" not in oidc._conf()["auth_endpoint"]


# --------------------------------------------------------------------------
# What must stay reachable without a session
# --------------------------------------------------------------------------

def test_auth_routes_are_exempt():
    """Otherwise signing in would require being signed in."""
    assert any("/auth/".startswith(p) or p == "/auth/"
               for p in oidc._OIDC_EXEMPT_PREFIXES)


def test_health_is_exempt():
    """Kubernetes probes carry no session and no API key."""
    assert "/health" in oidc._OIDC_EXEMPT_EXACT
    assert "/health/printer" in oidc._OIDC_EXEMPT_EXACT


def test_static_assets_are_exempt():
    """The sign-in page itself would be unstyled otherwise."""
    for prefix in ("/css/", "/js/"):
        assert prefix in oidc._OIDC_EXEMPT_PREFIXES


# --------------------------------------------------------------------------
# Session expiry
# --------------------------------------------------------------------------

def test_current_user_none_without_session(app_ctx):
    assert oidc.current_user() is None


def test_current_user_returns_signed_in_user(app_ctx):
    from flask import session
    session[oidc._SESSION_USER] = "ms"
    session[oidc._SESSION_EXPIRES] = time.time() + 3600
    assert oidc.current_user() == "ms"


def test_expired_session_is_rejected_and_cleared(app_ctx):
    """A stale cookie must not keep access alive."""
    from flask import session
    session[oidc._SESSION_USER] = "ms"
    session[oidc._SESSION_EXPIRES] = time.time() - 1
    assert oidc.current_user() is None
    assert oidc._SESSION_USER not in session


def test_session_without_expiry_is_rejected(app_ctx):
    """A cookie forged without the expiry key should not be honoured."""
    from flask import session
    session[oidc._SESSION_USER] = "ms"
    assert oidc.current_user() is None


# --------------------------------------------------------------------------
# id_token decoding
# --------------------------------------------------------------------------

def _id_token(claims):
    payload = base64.urlsafe_b64encode(
        json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


def test_claims_are_read_from_id_token():
    claims = oidc._claims_from_id_token(
        _id_token({"preferred_username": "ms", "nonce": "n"}))
    assert claims["preferred_username"] == "ms"
    assert claims["nonce"] == "n"


def test_missing_id_token_returns_none():
    assert oidc._claims_from_id_token(None) is None


def test_malformed_id_token_returns_none():
    assert oidc._claims_from_id_token("not-a-jwt") is None


# --------------------------------------------------------------------------
# PKCE
# --------------------------------------------------------------------------

def test_pkce_challenge_is_the_s256_of_the_verifier():
    import base64
    import hashlib

    verifier, challenge = oidc._pkce_pair()
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")
    assert challenge == expected


def test_pkce_challenge_has_no_padding():
    """RFC 7636 requires base64url without '=' padding; Keycloak rejects it."""
    _, challenge = oidc._pkce_pair()
    assert "=" not in challenge


def test_pkce_pair_is_random_each_time():
    assert oidc._pkce_pair()[0] != oidc._pkce_pair()[0]


def test_verifier_is_cleared_with_the_session(app_ctx):
    from flask import session
    session[oidc._SESSION_VERIFIER] = "v"
    session[oidc._SESSION_USER] = "ms"
    oidc._clear_session()
    assert oidc._SESSION_VERIFIER not in session


# --------------------------------------------------------------------------
# The framing constraints this exists for
# --------------------------------------------------------------------------

def test_signin_page_opens_keycloak_in_a_new_tab():
    """Keycloak sends frame-ancestors 'self', so it cannot render in the frame.

    Without target=_blank the dashboard shows a blank panel instead of a login
    page -- the exact symptom recorded in SSO-PLAN.md.
    """
    page = oidc._signin_page()
    assert 'target="_blank"' in page or "target='_blank'" in page
    assert "/auth/login" in page


def test_cookie_is_configured_for_framing(monkeypatch):
    """SameSite=None or the cookie is withheld on framed requests."""
    _configure()

    class FakeApp:
        def __init__(self):
            self.config = {}
            self.routes = []

        def route(self, rule, **kw):
            def deco(fn):
                self.routes.append(rule)
                return fn
            return deco

        def before_request(self, fn):
            return fn

    app = FakeApp()
    assert oidc.register_oidc(app) is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "None"
    assert app.config["SESSION_COOKIE_SECURE"] is True
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True


def test_incomplete_config_does_not_enable_and_does_not_crash():
    """A typo must not take the printer offline, but must not pretend either."""
    os.environ["OIDC_ENABLED"] = "true"

    class FakeApp:
        config = {}

        def route(self, *a, **kw):
            return lambda fn: fn

        def before_request(self, fn):
            return fn

    assert oidc.register_oidc(FakeApp()) is False
