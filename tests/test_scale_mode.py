"""scale_mode: font_size is an input, not a hint.

The behaviour these lock down is the one the old code got wrong -- auto_fit
defaulted True and was not listed in default_settings, so font_size was almost
always silently re-derived and the printed label did not match what the user
asked for.
"""
import pytest

from src.services.printer_service import PrinterService


resolve = PrinterService._resolve_scale_mode


class TestDefault:
    def test_no_settings_honours_font_size(self):
        """The whole point: absent any instruction, print what was asked for."""
        assert resolve({}, 60) == ("actual", 60)

    def test_rotation_does_not_imply_scaling(self):
        """Rotating a label must not silently change its font size.

        This is the specific surprise being removed: laying out lengthwise and
        scaling to fit are separate decisions.
        """
        assert resolve({"rotate": 90, "rotate_mode": "layout"}, 60) == ("actual", 60)


class TestBackwardCompatibility:
    """Existing callers (Canva poller, Homebox, saved settings) keep working."""

    def test_auto_fit_true_selects_fit(self):
        assert resolve({"auto_fit": True}, 60) == ("fit", 60)

    def test_auto_fit_false_selects_actual(self):
        assert resolve({"auto_fit": False}, 60) == ("actual", 60)

    def test_scale_mode_wins_over_auto_fit(self):
        assert resolve({"auto_fit": True, "scale_mode": "actual"}, 60) == ("actual", 60)


class TestCustom:
    def test_scales_font_size(self):
        assert resolve({"scale_mode": "custom", "scale_percent": 50}, 60) == ("custom", 30)

    def test_rounds_to_nearest_int(self):
        assert resolve({"scale_mode": "custom", "scale_percent": 33}, 60) == ("custom", 20)

    def test_percent_defaults_to_100(self):
        assert resolve({"scale_mode": "custom"}, 60) == ("custom", 60)

    def test_never_returns_zero(self):
        """A tiny percentage on a small font must still render something."""
        mode, size = resolve({"scale_mode": "custom", "scale_percent": 10}, 4)
        assert size >= 1

    @pytest.mark.parametrize("pct", [0, 9, 401, 1000, -50])
    def test_rejects_out_of_range(self, pct):
        with pytest.raises(ValueError, match="scale_percent"):
            resolve({"scale_mode": "custom", "scale_percent": pct}, 60)

    def test_rejects_non_numeric(self):
        with pytest.raises(ValueError, match="scale_percent"):
            resolve({"scale_mode": "custom", "scale_percent": "big"}, 60)


class TestValidation:
    @pytest.mark.parametrize("mode", ["fill", "shrink", "", "AUTO"])
    def test_rejects_unknown_mode(self, mode):
        with pytest.raises(ValueError, match="scale_mode"):
            resolve({"scale_mode": mode}, 60)

    @pytest.mark.parametrize("mode", ["actual", "fit", "custom"])
    def test_accepts_known_modes(self, mode):
        assert resolve({"scale_mode": mode}, 60)[0] == mode

    def test_case_insensitive(self):
        assert resolve({"scale_mode": "Fit"}, 60)[0] == "fit"

    def test_only_custom_scales(self):
        """actual and fit must leave font_size untouched even with a percent set."""
        for mode in ("actual", "fit"):
            assert resolve({"scale_mode": mode, "scale_percent": 50}, 60)[1] == 60
