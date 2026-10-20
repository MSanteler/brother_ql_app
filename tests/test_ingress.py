"""
Tests for running behind Home Assistant ingress.

The security-relevant property: trust comes from the connection's peer address
(the Supervisor's proxy), never from the ``X-Ingress-Path`` header, which any
client can send. The UI half: every URL the bundled UI requests must be
relative, or it escapes the ingress prefix and lands on Home Assistant itself.
"""

import pathlib
import re

import pytest
from flask import Flask, jsonify

from src.utils import auth

PROXY = "172.30.32.2"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ("INGRESS_PROXY", "API_KEY", "OIDC_ENABLED"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def client(monkeypatch):
    """A bare Flask app with the real API-key hook and one API route."""
    from src.app import register_auth

    monkeypatch.setenv("API_KEY", "machine-key")
    monkeypatch.setenv("INGRESS_PROXY", PROXY)
    app = Flask(__name__)
    register_auth(app)

    @app.route("/api/v1/thing")
    def thing():
        return jsonify(ok=True)

    return app.test_client()


def _get(client, addr, **headers):
    return client.get("/api/v1/thing", headers=headers,
                      environ_base={"REMOTE_ADDR": addr})


# --------------------------------------------------------------------------
# Opt-in
# --------------------------------------------------------------------------

def test_disabled_by_default():
    assert auth.ingress_enabled() is False
    app = Flask(__name__)
    with app.test_request_context("/", environ_base={"REMOTE_ADDR": PROXY}):
        assert auth.is_ingress_request() is False


# --------------------------------------------------------------------------
# Who is trusted
# --------------------------------------------------------------------------

def test_request_from_the_proxy_needs_no_key(client):
    resp = _get(client, PROXY, **{"X-Ingress-Path": "/api/hassio_ingress/abc"})
    assert resp.status_code == 200


def test_the_header_alone_is_not_a_credential(client):
    """Anyone can send X-Ingress-Path; only the proxy connects from its address."""
    resp = _get(client, "192.168.86.50", **{"X-Ingress-Path": "/api/hassio_ingress/abc"})
    assert resp.status_code == 401


def test_machine_callers_still_use_the_key(client):
    assert _get(client, "192.168.86.50", **{"X-API-Key": "machine-key"}).status_code == 200
    assert _get(client, "192.168.86.50").status_code == 401


def test_ingress_info_checks_the_address_not_the_value(monkeypatch):
    monkeypatch.setenv("INGRESS_PROXY", PROXY)
    app = Flask(__name__)
    with app.test_request_context("/", environ_base={"REMOTE_ADDR": "10.0.0.9"}):
        assert auth.ingress_info("/api/hassio_ingress/abc") is None
    with app.test_request_context("/", environ_base={"REMOTE_ADDR": PROXY}):
        assert auth.ingress_info("anything") == {"sub": "ingress"}


def test_prefix_only_comes_from_the_proxy(monkeypatch):
    """A redirect target taken from a client header would be an open redirect."""
    monkeypatch.setenv("INGRESS_PROXY", PROXY)
    app = Flask(__name__)
    headers = {"X-Ingress-Path": "/api/hassio_ingress/abc/"}
    with app.test_request_context("/", headers=headers,
                                  environ_base={"REMOTE_ADDR": PROXY}):
        assert auth.ingress_prefix() == "/api/hassio_ingress/abc"
    with app.test_request_context("/", headers=headers,
                                  environ_base={"REMOTE_ADDR": "10.0.0.9"}):
        assert auth.ingress_prefix() == ""


def test_oidc_gate_accepts_an_ingress_request(monkeypatch):
    from src.utils import oidc

    monkeypatch.setenv("OIDC_ENABLED", "true")
    monkeypatch.setenv("OIDC_ISSUER_URL", "https://auth.example.com/realms/r")
    monkeypatch.setenv("OIDC_CLIENT_ID", "label-printer")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "shh")
    monkeypatch.setenv("INGRESS_PROXY", PROXY)
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test-only"
    assert oidc.register_oidc(app) is True

    @app.route("/")
    def index():
        return "ui"

    c = app.test_client()
    assert c.get("/", environ_base={"REMOTE_ADDR": PROXY}).status_code == 200
    assert c.get("/", environ_base={"REMOTE_ADDR": "10.0.0.9"}).status_code != 200


# --------------------------------------------------------------------------
# connexion: a third alternative, present only when enabled
# --------------------------------------------------------------------------

def test_ingress_is_a_separate_security_entry():
    src = pathlib.Path("src/app.py").read_text()
    assert "spec['security'].append({'IngressAuth': []})" in src
    assert "schemes.pop('IngressAuth', None)" in src


# --------------------------------------------------------------------------
# The UI must stay inside the prefix
# --------------------------------------------------------------------------

def test_ui_requests_no_absolute_api_urls():
    """`/api/v1/...` from a page at /api/hassio_ingress/<token>/ hits HA, not us.

    demo.js is excluded: it intercepts fetches by resolved path and never
    requests anything itself.
    """
    static = pathlib.Path("src/static")
    offenders = []
    for f in list(static.glob("js/*.js")) + [static / "index.html"]:
        if f.name == "demo.js":
            continue
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if re.search(r"""['"`]/api/""", line):
                offenders.append(f"{f.name}:{n}: {line.strip()}")
    assert not offenders, "\n".join(offenders)
