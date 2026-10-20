"""
Tests for reading printer status over a tcp:// data port.

A USB QL behind a byte relay (9100 <-> USB bulk endpoints) answers the same
``ESC i S`` request over the socket that it answers over USB. These tests run a
real socket on localhost standing in for the relay, so the request bytes, the
32-byte read and the hang-up handling are exercised for real; only the printer
is simulated.
"""

import os
import socket
import threading

import pytest

from src.services import printer_service as ps
from src.services.printer_service import printer_service


def _packet(width=50, length=0, media=0x0A, err1=0, err2=0):
    """A QL status reply: header 80 20 42, width at 10, type at 11, length at 17."""
    data = bytearray(32)
    data[0:3] = b"\x80\x20\x42"
    data[3] = 0x34          # series
    data[4] = 0x38          # model
    data[8] = err1
    data[9] = err2
    data[10] = width
    data[11] = media        # 0x0A continuous, 0x0B die-cut
    data[17] = length
    data[18] = 0x00         # reply to status request
    data[19] = 0x00         # waiting to receive
    return bytes(data)


class FakeRelay:
    """One-shot TCP server. ``reply`` is sent once the request has arrived."""

    def __init__(self, reply=None, read_request=True):
        self.reply = reply
        self.read_request = read_request
        self.received = b""
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    @property
    def uri(self):
        return f"tcp://127.0.0.1:{self.port}"

    def _serve(self):
        conn, _ = self.sock.accept()
        with conn:
            if self.read_request:
                while len(self.received) < len(ps.STATUS_REQUEST):
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    self.received += chunk
            if self.reply:
                conn.sendall(self.reply)
        self.sock.close()


@pytest.fixture
def tcp_status(monkeypatch):
    monkeypatch.setenv("PRINTER_STATUS_OVER_TCP", "true")


# --------------------------------------------------------------------------
# The reader itself
# --------------------------------------------------------------------------

def test_reads_the_media_through_the_relay():
    relay = FakeRelay(reply=_packet(width=50))
    reachable, status = ps.read_tcp_printer_status(relay.uri, _retries=1)
    relay.thread.join(2)

    assert reachable is True
    assert status["media_width"] == 50
    assert status["errors"] == []
    # The same preamble the USB path sends: invalidate, initialise, ESC i S.
    assert relay.received == b"\x00" * 200 + b"\x1b\x40" + b"\x1b\x69\x53"


def test_relay_that_hangs_up_is_reachable_but_silent():
    """What the relay does when the printer is switched off."""
    relay = FakeRelay(reply=None)
    reachable, status = ps.read_tcp_printer_status(relay.uri, _retries=1)
    assert (reachable, status) == (True, None)


def test_short_reply_is_not_a_status():
    relay = FakeRelay(reply=_packet()[:10])
    assert ps.read_tcp_printer_status(relay.uri, _retries=1) == (True, None)


def test_garbage_reply_is_not_a_status_and_does_not_raise():
    relay = FakeRelay(reply=b"\xff" * 32)
    assert ps.read_tcp_printer_status(relay.uri, _retries=1) == (True, None)


def test_nothing_listening_is_unreachable():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert ps.read_tcp_printer_status(f"tcp://127.0.0.1:{port}", timeout=1,
                                      _retries=1) == (False, None)


def test_port_defaults_to_9100():
    assert ps._split_tcp_uri("tcp://brotherqlpi1.local") == ("brotherqlpi1.local", 9100)
    assert ps._split_tcp_uri("tcp://10.0.0.5:9101") == ("10.0.0.5", 9101)
    assert ps._split_tcp_uri("usb://0x04f9:0x209b") == (None, None)


# --------------------------------------------------------------------------
# Opt-in: unset, nothing changes
# --------------------------------------------------------------------------

def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("PRINTER_STATUS_OVER_TCP", raising=False)
    assert ps.tcp_status_enabled() is False


def test_media_status_ignores_tcp_unless_enabled(monkeypatch):
    monkeypatch.delenv("PRINTER_STATUS_OVER_TCP", raising=False)
    monkeypatch.setattr(ps, "read_tcp_printer_status",
                        lambda *a, **k: pytest.fail("must not probe the socket"))
    monkeypatch.setattr(ps, "read_usb_printer_status", lambda uri: None)
    assert ps.read_printer_media_status("tcp://10.0.0.5:9100") is None


def test_media_status_uses_tcp_when_enabled(tcp_status, monkeypatch):
    monkeypatch.setattr(ps, "read_tcp_printer_status",
                        lambda uri: (True, {"media_width": 62}))
    assert ps.read_printer_media_status("tcp://10.0.0.5:9100") == {"media_width": 62}


def test_usb_printers_are_unaffected_by_the_flag(tcp_status, monkeypatch):
    monkeypatch.setattr(ps, "read_tcp_printer_status",
                        lambda *a, **k: pytest.fail("usb must not go over tcp"))
    monkeypatch.setattr(ps, "read_usb_printer_status", lambda uri: {"media_width": 29})
    assert ps.read_printer_media_status("usb://0x04f9:0x209b") == {"media_width": 29}


# --------------------------------------------------------------------------
# The status endpoint
# --------------------------------------------------------------------------

def _no_ipp(*a, **k):
    pytest.fail("a relay has no IPP server; it must not be probed")


def test_status_reports_loaded_media(tcp_status, monkeypatch):
    from brother_ql.reader import interpret_response

    monkeypatch.setattr(ps, "get_printer_attributes", _no_ipp)
    monkeypatch.setattr(ps, "read_tcp_printer_status",
                        lambda uri: (True, interpret_response(_packet(width=50))))
    result = printer_service.check_printer_status("tcp://10.0.0.5:9100", "QL-800")

    assert result["available"] is True
    assert result["status"] == "Ready -- 50mm continuous loaded"
    assert result["details"]["source"] == "tcp-status"
    assert "50" in result["details"]["loadable_label_sizes"]


def test_status_says_when_the_printer_is_off(tcp_status, monkeypatch):
    monkeypatch.setattr(ps, "get_printer_attributes", _no_ipp)
    monkeypatch.setattr(ps, "read_tcp_printer_status", lambda uri: (True, None))
    result = printer_service.check_printer_status("tcp://10.0.0.5:9100", "QL-800")

    assert result["available"] is False
    assert "not answering" in result["status"]


def test_status_says_when_the_relay_is_down(tcp_status, monkeypatch):
    monkeypatch.setattr(ps, "get_printer_attributes", _no_ipp)
    monkeypatch.setattr(ps, "read_tcp_printer_status", lambda uri: (False, None))
    result = printer_service.check_printer_status("tcp://10.0.0.5:9100", "QL-800")

    assert result["available"] is False
    assert result["status"] == "Printer not reachable"


def test_status_reports_printer_errors(tcp_status, monkeypatch):
    from brother_ql.reader import interpret_response

    monkeypatch.setattr(ps, "get_printer_attributes", _no_ipp)
    # error_info_1 bit 0 is "No media when printing".
    monkeypatch.setattr(ps, "read_tcp_printer_status",
                        lambda uri: (True, interpret_response(_packet(err1=0x01))))
    result = printer_service.check_printer_status("tcp://10.0.0.5:9100", "QL-800")

    assert result["available"] is False
    assert result["status"].startswith("Printer reports: ")


# --------------------------------------------------------------------------
# The media guard covers relayed printers too
# --------------------------------------------------------------------------

def test_submit_guard_rejects_a_mismatch_through_the_relay(tcp_status, monkeypatch):
    from src.utils.exceptions import ValidationError
    from src.utils.print_guard import enforce_media_match

    monkeypatch.setattr(ps, "read_tcp_printer_status",
                        lambda uri: (True, {"media_width": 50, "media_length": 0}))
    with pytest.raises(ValidationError):
        enforce_media_match({"printer_uri": "tcp://10.0.0.5:9100", "label_size": "62"})


def test_submit_guard_allows_a_silent_relay(tcp_status, monkeypatch):
    """Unreadable is not a mismatch: a sleeping printer must still print."""
    from src.utils.print_guard import enforce_media_match

    monkeypatch.setattr(ps, "read_tcp_printer_status", lambda uri: (True, None))
    enforce_media_match({"printer_uri": "tcp://10.0.0.5:9100", "label_size": "62"})
