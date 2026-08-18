"""
Tests for src/services/settings_service.py::SettingsService.

These tests always use a temporary settings file (``tmp_path``) — never the real
``/app/data`` or ``data/settings.json``. ``brother_ql.backends.guess_backend`` is
patched where keep-alive validation is exercised so the tests behave identically
whether or not the real brother_ql package is installed (the conftest stub maps
tcp:// -> "network" and usb:// -> "pyusb", matching the patched behaviour).
"""

import json
from unittest.mock import patch

import pytest

from src.services.settings_service import SettingsService


def _valid_settings(**overrides):
    base = {
        "printer_uri": "tcp://10.50.60.20",
        "printer_model": "QL-800",
        "label_size": "62",
        "font_size": 50,
        "alignment": "left",
        "rotate": 0,
        "threshold": 70.0,
        "dither": False,
        "compress": False,
        "red": False,
        "keep_alive_enabled": False,
        "keep_alive_interval": 60,
        "ipp_port": 631,
        "printers": [
            {
                "id": "default",
                "name": "Default Printer",
                "printer_uri": "tcp://10.50.60.20",
                "printer_model": "QL-800",
                "label_size": "62",
            }
        ],
    }
    base.update(overrides)
    return base


def _make_service(tmp_path, settings=None):
    path = tmp_path / "settings.json"
    if settings is not None:
        path.write_text(json.dumps(settings), encoding="utf-8")
    return SettingsService(settings_file=str(path)), path


def _net_backend(uri):
    """Deterministic guess_backend used in keep-alive validation tests."""
    return "network" if str(uri).startswith("tcp://") else "pyusb"


# --- mtime cache -------------------------------------------------------------

def test_get_settings_uses_mtime_cache(tmp_path):
    service, _ = _make_service(tmp_path, _valid_settings())

    # The constructor already called _load_settings once; reset the counter.
    with patch.object(service, "_load_settings", wraps=service._load_settings) as load:
        first = service.get_settings()
        second = service.get_settings()
        # Same unchanged file -> only ONE disk load across both calls.
        assert load.call_count == 1

    assert first["printer_uri"] == "tcp://10.50.60.20"
    assert second["printer_uri"] == "tcp://10.50.60.20"


def test_cache_refreshes_after_save_settings(tmp_path):
    service, _ = _make_service(tmp_path, _valid_settings())
    assert service.get_settings()["font_size"] == 50

    with patch("src.services.settings_service.guess_backend", side_effect=_net_backend):
        assert service.save_settings(_valid_settings(font_size=99)) is True

    # Fresh value visible immediately (cache refreshed on save).
    assert service.get_settings()["font_size"] == 99


def test_update_settings_merges_and_persists(tmp_path):
    service, path = _make_service(tmp_path, _valid_settings())

    with patch("src.services.settings_service.guess_backend", side_effect=_net_backend):
        assert service.update_settings({"alignment": "center"}) is True

    settings = service.get_settings()
    assert settings["alignment"] == "center"
    # Untouched key preserved through the merge.
    assert settings["printer_model"] == "QL-800"
    # Persisted to disk.
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["alignment"] == "center"


# --- validation --------------------------------------------------------------

def test_valid_settings_pass_validation(tmp_path):
    service, _ = _make_service(tmp_path)
    with patch("src.services.settings_service.guess_backend", side_effect=_net_backend):
        # Should not raise.
        service._validate_settings(_valid_settings())


def test_ipp_port_out_of_range_rejected(tmp_path):
    service, _ = _make_service(tmp_path)
    with patch("src.services.settings_service.guess_backend", side_effect=_net_backend):
        with pytest.raises(ValueError):
            service._validate_settings(_valid_settings(ipp_port=70000))


def test_empty_printer_uri_rejected(tmp_path):
    service, _ = _make_service(tmp_path)
    with patch("src.services.settings_service.guess_backend", side_effect=_net_backend):
        with pytest.raises(ValueError):
            service._validate_settings(_valid_settings(printer_uri="   "))


def test_file_scheme_uri_rejected(tmp_path):
    service, _ = _make_service(tmp_path)
    with patch("src.services.settings_service.guess_backend", side_effect=_net_backend):
        with pytest.raises(ValueError):
            service._validate_settings(_valid_settings(printer_uri="file:///etc/passwd"))


def test_keep_alive_with_non_network_uri_rejected(tmp_path):
    service, _ = _make_service(tmp_path)
    settings = _valid_settings(
        printer_uri="usb://0x04f9:0x209c",
        keep_alive_enabled=True,
        keep_alive_interval=60,
        printers=[
            {
                "id": "default",
                "name": "USB Printer",
                "printer_uri": "usb://0x04f9:0x209c",
                "printer_model": "QL-800",
                "label_size": "62",
            }
        ],
    )
    with patch("src.services.settings_service.guess_backend", side_effect=_net_backend):
        with pytest.raises(ValueError):
            service._validate_settings(settings)


def test_save_settings_returns_false_on_invalid(tmp_path):
    service, _ = _make_service(tmp_path)
    with patch("src.services.settings_service.guess_backend", side_effect=_net_backend):
        # save_settings swallows validation errors and returns False.
        assert service.save_settings(_valid_settings(ipp_port=0)) is False


# --- deepcopy isolation ------------------------------------------------------

def test_returned_settings_are_isolated_from_cache(tmp_path):
    service, _ = _make_service(tmp_path, _valid_settings())

    first = service.get_settings()
    first["printer_model"] = "MUTATED"
    first["printers"][0]["label_size"] = "MUTATED"

    second = service.get_settings()
    assert second["printer_model"] == "QL-800"
    assert second["printers"][0]["label_size"] == "62"


def test_missing_file_falls_back_to_defaults(tmp_path):
    # No file written -> defaults are returned, nothing cached.
    service, _ = _make_service(tmp_path, settings=None)
    settings = service.get_settings()
    assert settings["printer_model"] == "QL-800"
    assert "printers" in settings


class TestFontSettings:
    """font_style is a closed set; font_family deliberately is not validated.

    A settings file may legitimately name a face that is not installed at this
    moment -- a drop-in font not yet copied onto the volume, or a config
    restored onto a fresh container. Rejecting the save would lock the user out
    of changing every *other* setting because of a cosmetic mismatch, so the
    miss is handled at render time by falling back to the default face.
    """

    @pytest.mark.parametrize("style", ["regular", "bold", "italic", "bold_italic"])
    def test_accepts_every_style(self, tmp_path, style):
        service = SettingsService(settings_file=str(tmp_path / "settings.json"))
        assert service.save_settings(_valid_settings(font_style=style)) is True

    @pytest.mark.parametrize("style", ["Bold", "oblique", "semibold", ""])
    def test_rejects_an_unknown_style(self, tmp_path, style):
        service = SettingsService(settings_file=str(tmp_path / "settings.json"))
        with pytest.raises(ValueError, match="font_style"):
            service._validate_settings(_valid_settings(font_style=style))

    def test_accepts_a_family_that_is_not_installed(self, tmp_path):
        service = SettingsService(settings_file=str(tmp_path / "settings.json"))
        assert service.save_settings(
            _valid_settings(font_family="Some Font Not Installed Here")) is True

    def test_rejects_a_non_string_family(self, tmp_path):
        service = SettingsService(settings_file=str(tmp_path / "settings.json"))
        with pytest.raises(ValueError, match="font_family"):
            service._validate_settings(_valid_settings(font_family=42))

    def test_font_is_inherited_by_a_print_request(self, tmp_path):
        """A caller that posts no settings (Homebox, the Canva poller) must
        still get the configured typeface, not the built-in default."""
        service = SettingsService(settings_file=str(tmp_path / "settings.json"))
        service.save_settings(
            _valid_settings(font_family="Liberation Mono", font_style="italic"))

        resolved = service.resolve_print_settings(None)
        assert resolved["font_family"] == "Liberation Mono"
        assert resolved["font_style"] == "italic"

    def test_request_overrides_the_saved_font(self, tmp_path):
        service = SettingsService(settings_file=str(tmp_path / "settings.json"))
        service.save_settings(_valid_settings(font_family="Liberation Mono"))

        resolved = service.resolve_print_settings({"font_family": "DejaVu Serif"})
        assert resolved["font_family"] == "DejaVu Serif"
