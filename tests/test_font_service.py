"""Font selection: what gets catalogued, and what a request resolves to.

Two behaviours carry most of the weight here.

The first is that a face's *identity* comes from its name table, not its
filename. DejaVu ships every condensed and ExtraLight cut under the family
"DejaVu Sans", with the distinction recorded only in the style string
("Condensed Bold Oblique"). Reducing that to bold/italic collapses four
distinct faces onto one key, so most of them vanish from the catalog and which
survivor wins depends on directory order -- asking for regular DejaVu Sans could
hand back the ExtraLight.

The second is that resolution never fails. A settings file may name a font that
is not installed right now (a drop-in not yet copied onto the volume, a config
restored onto a fresh container). Falling back to the default face prints the
wrong typeface; raising does not print at all.
"""
import os

import pytest

from src.services import font_service as fs
from src.services.font_service import (
    FontService,
    _split_family_and_style,
    _split_style_name,
    apply_text_font,
)


def _catalog(monkeypatch, tmp_path, faces):
    """Build a FontService over *faces*: {filename: (family, style)}.

    The files are empty, so Pillow cannot read a name table from them; the
    identity is injected instead. That keeps these tests independent of which
    font packages happen to be installed on the machine running them.
    """
    for name in faces:
        (tmp_path / name).write_bytes(b"")

    monkeypatch.setattr(fs, "SYSTEM_FONT_DIRS", ())
    monkeypatch.setattr(
        FontService, "_face_identity",
        staticmethod(lambda path, stem: faces[os.path.basename(path)]),
    )
    return FontService(user_font_dir=str(tmp_path))


class TestStyleNameSplitting:
    """The name table's style string is more than weight and slant."""

    @pytest.mark.parametrize("style,bucket", [
        ("Regular", "regular"),
        ("Bold", "bold"),
        ("Italic", "italic"),
        ("Oblique", "italic"),
        ("Bold Italic", "bold_italic"),
        ("Bold Oblique", "bold_italic"),
        ("BoldItalic", "bold_italic"),
        ("Book", "regular"),
        ("", "regular"),
        (None, "regular"),
    ])
    def test_bucket(self, style, bucket):
        assert _split_style_name(style)[0] == bucket

    def test_width_is_kept_as_a_qualifier(self):
        """Otherwise the condensed cut overwrites the normal one."""
        assert _split_style_name("Condensed Bold Oblique") == (
            "bold_italic", ["Condensed"])

    def test_weight_that_is_not_bold_is_a_qualifier(self):
        assert _split_style_name("ExtraLight") == ("regular", ["ExtraLight"])

    def test_semibold_is_not_bold(self):
        """SemiBold is its own weight; folding it into bold loses a face."""
        assert _split_style_name("SemiBold") == ("regular", ["SemiBold"])


class TestCatalog:
    def test_groups_styles_under_one_family(self, monkeypatch, tmp_path):
        service = _catalog(monkeypatch, tmp_path, {
            "a.ttf": ("DejaVu Sans", "regular"),
            "b.ttf": ("DejaVu Sans", "bold"),
            "c.ttf": ("DejaVu Sans", "italic"),
        })
        families = service.list_families()
        assert len(families) == 1
        assert families[0]["family"] == "DejaVu Sans"
        assert families[0]["styles"] == ["regular", "bold", "italic"]

    def test_condensed_does_not_shadow_the_normal_cut(self, monkeypatch, tmp_path):
        """The regression this whole design exists to avoid."""
        service = _catalog(monkeypatch, tmp_path, {
            "DejaVuSans.ttf": ("DejaVu Sans", "regular"),
            "DejaVuSansCondensed.ttf": ("DejaVu Sans Condensed", "regular"),
        })
        assert {f["family"] for f in service.list_families()} == {
            "DejaVu Sans", "DejaVu Sans Condensed"}
        assert service.resolve("DejaVu Sans", "regular").endswith("DejaVuSans.ttf")

    def test_only_font_extensions_are_catalogued(self, monkeypatch, tmp_path):
        (tmp_path / "README.md").write_bytes(b"not a font")
        service = _catalog(monkeypatch, tmp_path, {"x.ttf": ("Only", "regular")})
        assert [f["family"] for f in service.list_families()] == ["Only"]

    def test_drop_in_faces_are_flagged(self, monkeypatch, tmp_path):
        service = _catalog(monkeypatch, tmp_path, {"x.ttf": ("Mine", "regular")})
        assert service.list_families()[0]["user_supplied"] is True

    def test_system_internal_faces_are_hidden(self, monkeypatch, tmp_path):
        """macOS ships dot-prefixed internal faces; no font picker shows them."""
        service = _catalog(monkeypatch, tmp_path, {
            "hidden.ttf": (".LastResort", "regular"),
            "real.ttf": ("Real Font", "regular"),
        })
        assert [f["family"] for f in service.list_families()] == ["Real Font"]

    def test_collision_resolves_the_same_way_every_scan(self, monkeypatch, tmp_path):
        """Two files claiming one slot must not depend on os.walk order."""
        service = _catalog(monkeypatch, tmp_path, {
            "a-copy.ttf": ("Same", "bold"),
            "b-copy.ttf": ("Same", "bold"),
        })
        first = service.resolve("Same", "bold")
        for _ in range(5):
            service.refresh()
            assert service.resolve("Same", "bold") == first

    def test_new_drop_in_is_picked_up_without_a_restart(self, monkeypatch, tmp_path):
        faces = {"a.ttf": ("First", "regular")}
        service = _catalog(monkeypatch, tmp_path, faces)
        assert [f["family"] for f in service.list_families()] == ["First"]

        (tmp_path / "b.ttf").write_bytes(b"")
        faces["b.ttf"] = ("Second", "regular")
        service.refresh()  # stands in for the rescan interval elapsing
        assert {f["family"] for f in service.list_families()} == {"First", "Second"}


class TestResolve:
    @pytest.fixture
    def service(self, monkeypatch, tmp_path):
        return _catalog(monkeypatch, tmp_path, {
            "ds.ttf": ("DejaVu Sans", "regular"),
            "db.ttf": ("DejaVu Sans", "bold"),
            "di.ttf": ("DejaVu Sans", "italic"),
            "dbi.ttf": ("DejaVu Sans", "bold_italic"),
            "lr.ttf": ("Liberation Serif", "regular"),
            "lb.ttf": ("Liberation Serif", "bold"),
        })

    def test_exact_match(self, service):
        assert service.resolve("Liberation Serif", "bold").endswith("lb.ttf")

    def test_family_match_ignores_case_and_spacing(self, service):
        """"dejavusans" is what a filename-derived config would carry."""
        assert service.resolve("dejavusans", "italic").endswith("di.ttf")
        assert service.resolve("DEJAVU  SANS", "italic").endswith("di.ttf")

    def test_defaults_to_the_historical_face(self, service):
        """No family, no style must render exactly as it did before fonts were
        selectable: DejaVu Sans Bold, the old hardcoded path."""
        assert service.resolve().endswith("db.ttf")
        assert service.default_family() == "DejaVu Sans"

    def test_unknown_family_falls_back_instead_of_raising(self, service):
        assert service.resolve("No Such Font", "bold").endswith("db.ttf")

    def test_unknown_style_falls_back_to_default_style(self, service):
        assert service.resolve("Liberation Serif", "nonsense").endswith("lb.ttf")

    def test_missing_style_degrades_to_the_closest(self, service):
        """Liberation Serif has no italic here; upright beats nothing."""
        assert service.resolve("Liberation Serif", "italic").endswith("lr.ttf")
        assert service.resolve("Liberation Serif", "bold_italic").endswith("lb.ttf")

    def test_empty_catalog_returns_none(self, monkeypatch, tmp_path):
        """The caller then uses Pillow's built-in font rather than crashing."""
        service = _catalog(monkeypatch, tmp_path, {})
        assert service.resolve("Anything", "bold") is None


class TestPathForId:
    @pytest.fixture
    def service(self, monkeypatch, tmp_path):
        return _catalog(monkeypatch, tmp_path, {
            "a.ttf": ("Liberation Serif", "italic"),
        })

    def test_resolves_a_catalogued_face(self, service):
        assert service.path_for_id("Liberation Serif:italic").endswith("a.ttf")

    @pytest.mark.parametrize("font_id", [
        "../../etc/passwd:bold",
        "/etc/passwd:bold",
        "Unknown Family:bold",
        "",
    ])
    def test_refuses_anything_not_in_the_catalog(self, service, font_id):
        """The endpoint serves files, so this is the boundary that stops a
        crafted id turning into an arbitrary file read."""
        assert service.path_for_id(font_id) is None


class TestFilenameFallback:
    """Used only when a font's name table cannot be read (corrupt drop-in, or
    Pillow stubbed out). It should still get the Debian filenames right."""

    @pytest.mark.parametrize("stem,expected", [
        ("DejaVuSans-Bold", ("DejaVu Sans", "bold")),
        ("DejaVuSans-BoldOblique", ("DejaVu Sans", "bold_italic")),
        ("DejaVuSans-Oblique", ("DejaVu Sans", "italic")),
        ("DejaVuSansMono", ("DejaVu Sans Mono", "regular")),
        ("LiberationSerif-Regular", ("Liberation Serif", "regular")),
        ("LiberationMono-BoldItalic", ("Liberation Mono", "bold_italic")),
    ])
    def test_debian_font_filenames(self, stem, expected):
        family, style = _split_family_and_style(stem)
        # The family is camel-case split, so compare insensitively to spacing:
        # only the style bucket and the letters matter for lookup.
        assert style == expected[1]
        assert family.replace(" ", "").lower() == expected[0].replace(" ", "").lower()

    def test_a_family_word_is_not_mistaken_for_a_style(self):
        """"Roman" in "Times New Roman" is part of the name, not the style."""
        assert _split_family_and_style("TimesNewRoman")[1] == "regular"
        assert "Roman" in _split_family_and_style("TimesNewRoman")[0]


class TestApplyTextFont:
    """The composite layouts namespace their text options with a text_ prefix."""

    def test_copies_into_prefixed_keys(self):
        combined = {}
        apply_text_font(combined, {"font_family": "Inter", "font_style": "bold"})
        assert combined == {
            "text_font_family": "Inter", "text_font_style": "bold"}

    @pytest.mark.parametrize("text_settings", [
        {}, None, {"font_family": ""}, {"font_family": None, "font_style": ""},
    ])
    def test_absent_font_does_not_shadow_the_label_level_one(self, text_settings):
        """Writing an empty string here would override the label's own setting
        with "no choice", which is not the same as leaving it alone."""
        combined = {"font_family": "Label Level"}
        apply_text_font(combined, text_settings)
        assert combined == {"font_family": "Label Level"}


class TestResolveFontPathPrecedence:
    """Which font a given render actually picks.

    The composite layouts already namespace their text options ("text_font_size"),
    and family/style follow the same rule: the block's own choice wins, then the
    label's, then the catalog default. Getting this backwards would make the QR
    caption silently ignore the typeface chosen for it.
    """

    @pytest.fixture
    def service(self, monkeypatch):
        from src.services import printer_service as ps

        seen = {}

        class _Catalog:
            user_font_dir = "/tmp/fonts"

            def resolve(self, family=None, style=None):
                seen["family"], seen["style"] = family, style
                return "/fake/font.ttf"

        monkeypatch.setattr(ps, "font_service", _Catalog())
        svc = ps.PrinterService.__new__(ps.PrinterService)
        svc.font_path = "/fake/default.ttf"
        return svc, seen

    def test_unprefixed_keys_are_used_by_the_plain_text_label(self, service):
        svc, seen = service
        svc._resolve_font_path({"font_family": "Inter", "font_style": "italic"})
        assert seen == {"family": "Inter", "style": "italic"}

    def test_prefixed_keys_win_for_a_composite_layout(self, service):
        svc, seen = service
        svc._resolve_font_path(
            {"font_family": "Label Level", "font_style": "bold",
             "text_font_family": "Caption", "text_font_style": "italic"},
            prefix="text_")
        assert seen == {"family": "Caption", "style": "italic"}

    def test_prefixed_falls_through_to_the_label_level_choice(self, service):
        """A QR label with no caption-specific font uses the label's."""
        svc, seen = service
        svc._resolve_font_path(
            {"font_family": "Label Level", "font_style": "bold"}, prefix="text_")
        assert seen == {"family": "Label Level", "style": "bold"}

    def test_empty_strings_do_not_shadow(self, service):
        """The UI sends "" for "use the default", not a family named ""."""
        svc, seen = service
        svc._resolve_font_path(
            {"font_family": "Label Level", "text_font_family": "",
             "text_font_style": ""},
            prefix="text_")
        assert seen == {"family": "Label Level", "style": None}

    def test_falls_back_to_the_instance_font_when_nothing_resolves(self, monkeypatch):
        """An empty catalog must not take the print down."""
        from src.services import printer_service as ps

        class _Empty:
            user_font_dir = "/tmp/fonts"

            def resolve(self, family=None, style=None):
                return None

        monkeypatch.setattr(ps, "font_service", _Empty())
        svc = ps.PrinterService.__new__(ps.PrinterService)
        svc.font_path = "/fake/default.ttf"
        assert svc._resolve_font_path({}) == "/fake/default.ttf"
