"""
Font catalog for text rendering.

Discovers the TrueType/OpenType faces available to the app and groups them into
*families* (DejaVu Sans, Liberation Mono, ...) each carrying up to four *styles*
(regular / bold / italic / bold_italic). Callers ask for a family and a style and
get back a path they can hand to ``ImageFont.truetype``.

Two sources, in precedence order:

1. ``FONTS_DIR`` (default ``/app/data/fonts``) -- the drop-in directory. It lives
   on the persistent volume, so a face copied in there survives a container
   rebuild and needs no image change. Scanned recursively.
2. The system font directories baked into the image (``fonts-dejavu``,
   ``fonts-liberation``).

The drop-in directory wins on a name collision, so a user can shadow a bundled
face with their own cut of it.

Resolution never raises for an unknown font: it falls back to the configured
default family, and finally to *any* face it can find. A label printing in the
wrong typeface is a nuisance; a label that refuses to print because someone
deleted a .ttf is an outage.
"""

import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import structlog

logger = structlog.get_logger()

# Where a user may drop their own faces. On the persistent volume by design --
# /app/data is the only path that survives a rebuild of the image.
DEFAULT_USER_FONT_DIR = "/app/data/fonts"

# System directories shipped in the image. Missing directories are skipped, so
# this list is safe to run against a bare dev checkout on macOS too.
SYSTEM_FONT_DIRS = (
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/truetype/liberation",
    "/usr/share/fonts/truetype/liberation2",
    "/usr/share/fonts/truetype",
    "/usr/local/share/fonts",
    "/Library/Fonts",  # macOS, for running the app outside Docker
    "/System/Library/Fonts",
)

FONT_EXTENSIONS = (".ttf", ".otf", ".ttc")

# The historical hardcoded face. Kept as the default so existing labels render
# byte-identically after this change.
DEFAULT_FAMILY = "DejaVu Sans"
DEFAULT_STYLE = "bold"

STYLES = ("regular", "bold", "italic", "bold_italic")

# Style tokens as they appear in font *filenames*, compound forms first so that
# "BoldOblique" is matched before "Bold". Each maps to a (bold, italic) pair.
#
# Deliberately short: "Book" and "Roman" are style names in some foundries'
# catalogues, but as filename tokens they collide with real family names
# ("Bookman", "Times New Roman"), and losing the family is worse than missing a
# style. Anything unrecognised becomes the regular face of its own family, which
# still renders -- just under a longer name in the dropdown.
_STYLE_TOKENS: Tuple[Tuple[str, Tuple[bool, bool]], ...] = (
    ("bolditalic", (True, True)),
    ("boldoblique", (True, True)),
    ("italicbold", (True, True)),
    ("obliquebold", (True, True)),
    ("bold", (True, False)),
    ("italic", (False, True)),
    ("oblique", (False, True)),
    ("regular", (False, False)),
)


def _split_family_and_style(stem: str) -> Tuple[str, str]:
    """
    Derive ``(family, style)`` from a font filename stem.

    Handles the conventions that actually appear in the Debian font packages:
    ``DejaVuSans-BoldOblique`` and ``LiberationSerif-Italic`` (separator before
    the style), and ``ArialBold`` (style welded onto the end). A stem with no
    recognisable style token is the regular face of its own family, so
    ``DejaVuSansCondensed`` stays one family rather than being mangled.
    """
    head, sep, tail = _rpartition_any(stem, "-_ ")

    # Prefer the tail after a separator: it is unambiguous. Only fall back to
    # matching the *end* of the whole stem -- matching anywhere inside it would
    # find "italic" in a family that merely contains the letters.
    bold = italic = False
    family_raw = stem
    matched = False

    tail_key = re.sub(r"[^a-z]", "", tail.lower()) if sep else ""
    if tail_key:
        for token, flags in _STYLE_TOKENS:
            if tail_key == token:
                bold, italic = flags
                family_raw = head
                matched = True
                break

    if not matched:
        stem_key = re.sub(r"[^a-z]", "", stem.lower())
        for token, flags in _STYLE_TOKENS:
            if stem_key.endswith(token) and len(stem_key) > len(token):
                bold, italic = flags
                # Trim the same number of *letters* off the original stem,
                # keeping any punctuation that preceded them.
                family_raw = _trim_letters(stem, len(token))
                break

    style = "regular"
    if bold and italic:
        style = "bold_italic"
    elif bold:
        style = "bold"
    elif italic:
        style = "italic"

    return _humanise_family(family_raw), style


# Style words that map onto our four buckets. Anything else in a style name is
# a family *qualifier* (see _split_style_name).
_BOLD_WORDS = {"bold"}
_ITALIC_WORDS = {"italic", "oblique"}
_NEUTRAL_WORDS = {"regular", "book", "roman", "normal", "plain", ""}
# Style names sometimes arrive welded together rather than space-separated.
_COMPOUND_WORDS = {
    "bolditalic": (True, True),
    "boldoblique": (True, True),
    "italicbold": (True, True),
    "obliquebold": (True, True),
}


def _split_style_name(style: Optional[str]) -> Tuple[str, List[str]]:
    """
    Split a font's own style name into ``(bucket, qualifiers)``.

    A name table's style string carries more than weight and slant. DejaVu ships
    every condensed face under family "DejaVu Sans" with the width recorded in
    the style ("Condensed Bold Oblique"), and the ExtraLight cut the same way.
    Reducing those to just bold/italic makes four distinct faces collide on one
    key, so the catalog silently drops most of them and which survivor wins
    depends on directory order -- asking for regular DejaVu Sans could hand back
    the ExtraLight.

    So: words we understand as weight or slant pick the bucket; every other word
    ("Condensed", "ExtraLight", "SemiBold") is returned as a qualifier for the
    caller to append to the family name, giving it a slot of its own.
    """
    bold = italic = False
    qualifiers: List[str] = []

    for word in re.split(r"[\s\-_]+", (style or "").strip()):
        key = re.sub(r"[^a-z]", "", word.lower())
        if key in _COMPOUND_WORDS:
            is_bold, is_italic = _COMPOUND_WORDS[key]
            bold, italic = bold or is_bold, italic or is_italic
        elif key in _BOLD_WORDS:
            bold = True
        elif key in _ITALIC_WORDS:
            italic = True
        elif key in _NEUTRAL_WORDS:
            continue
        else:
            qualifiers.append(word.strip())

    if bold and italic:
        bucket = "bold_italic"
    elif bold:
        bucket = "bold"
    elif italic:
        bucket = "italic"
    else:
        bucket = "regular"

    return bucket, qualifiers


def _normalise_style_name(style: Optional[str]) -> str:
    """The style bucket alone, for callers that do not care about qualifiers."""
    return _split_style_name(style)[0]


def _trim_letters(stem: str, count: int) -> str:
    """
    Drop the last *count* alphabetic characters from *stem*.

    Walks backwards over the original string rather than the normalised key, so
    a stem like ``Arial-Bold`` loses ``Bold`` and the stray hyphen is cleaned up
    by ``_humanise_family``.
    """
    remaining = count
    idx = len(stem)
    while idx > 0 and remaining > 0:
        idx -= 1
        if stem[idx].isalpha():
            remaining -= 1
    return stem[:idx]


def _rpartition_any(text: str, separators: str) -> Tuple[str, str, str]:
    """``str.rpartition`` over several separator characters at once."""
    best = -1
    best_sep = ""
    for sep in separators:
        idx = text.rfind(sep)
        if idx > best:
            best, best_sep = idx, sep
    if best < 0:
        return text, "", ""
    return text[:best], best_sep, text[best + 1:]


def _humanise_family(raw: str) -> str:
    """
    Turn a filename fragment into a display family name.

    ``DejaVuSansMono`` -> ``DejaVu Sans Mono``; ``Liberation_Serif`` ->
    ``Liberation Serif``. Runs of capitals (``DVSans``) are kept intact so
    acronyms are not shredded into single letters.
    """
    spaced = raw.replace("_", " ").replace("-", " ")
    # Insert a space at lower->upper boundaries and before a capital that starts
    # a new word after an acronym (e.g. "DVSans" -> "DV Sans").
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", spaced)
    spaced = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", spaced)
    return " ".join(spaced.split())


class FontService:
    """
    Builds and caches the font catalog.

    The catalog is rebuilt when the drop-in directory's mtime changes, so a face
    copied in with ``scp`` shows up on the next request without a restart. System
    directories are only scanned once -- they cannot change without a new image.
    """

    # Rescans are cheap (a directory listing) but not free, and the catalog is
    # read on every render. Re-check the drop-in directory at most this often.
    _RESCAN_INTERVAL_SECONDS = 5.0

    def __init__(self, user_font_dir: Optional[str] = None):
        self.user_font_dir = (
            user_font_dir
            or os.environ.get("FONTS_DIR")
            or DEFAULT_USER_FONT_DIR
        )
        self._lock = threading.Lock()
        self._catalog: Optional[Dict[str, Dict[str, str]]] = None
        self._last_scan_at = 0.0
        self._last_user_dir_stamp: Optional[Tuple[float, int]] = None

        # Create the drop-in directory if it is missing. The image creates it at
        # build time, but the usual deployment bind-mounts a host directory over
        # /app/data, which masks it -- so without this the place to put fonts
        # simply would not exist on a normal install. Failure is not fatal: a
        # read-only mount just means no user fonts, and the bundled ones still
        # work.
        try:
            os.makedirs(self.user_font_dir, exist_ok=True)
        except OSError as e:
            logger.info("Could not create user font directory; "
                        "only bundled fonts will be available",
                        directory=self.user_font_dir, error=str(e))

    # --- discovery -------------------------------------------------------

    def _scan_dir(self, directory: str, recursive: bool) -> List[Tuple[str, str]]:
        """Return ``(stem, path)`` for every font file under *directory*."""
        found: List[Tuple[str, str]] = []
        if not os.path.isdir(directory):
            return found
        try:
            if recursive:
                walker = (
                    (root, files)
                    for root, _dirs, files in os.walk(directory)
                )
            else:
                walker = ((directory, os.listdir(directory)),)
            for root, files in walker:
                for name in files:
                    stem, ext = os.path.splitext(name)
                    if ext.lower() not in FONT_EXTENSIONS:
                        continue
                    found.append((stem, os.path.join(root, name)))
        except OSError as e:
            logger.warning("Could not scan font directory",
                           directory=directory, error=str(e))
        return found

    @staticmethod
    def _face_identity(path: str, stem: str) -> Tuple[str, str]:
        """
        Determine ``(family, style)`` for one font file.

        Reads the font's own name table via Pillow, which is authoritative:
        DejaVu's filenames say ``DejaVuSans``, and no amount of camel-case
        splitting can know that is "DejaVu Sans" and not "Deja Vu Sans". The
        name table simply says so. It also gets weights like SemiBold right,
        which a filename parser turns into "Semi" + bold.

        Falls back to parsing the filename when the file cannot be read -- a
        corrupt drop-in, an exotic container, or a bare test environment where
        Pillow is stubbed out. A font we cannot name is still a font we can
        offer, just under a guessed name.
        """
        try:
            from PIL import ImageFont
            family, style = ImageFont.truetype(path, 10).getname()
            if family:
                bucket, qualifiers = _split_style_name(style)
                # "DejaVu Sans" + ["Condensed"] -> "DejaVu Sans Condensed", so
                # the condensed cut gets its own slot instead of overwriting the
                # normal one. Qualifiers already present in the family name are
                # not repeated ("Inter SemiBold" + ["SemiBold"]).
                parts = [family.strip()]
                existing = re.sub(r"[^a-z0-9]", "", family.lower())
                for qualifier in qualifiers:
                    key = re.sub(r"[^a-z0-9]", "", qualifier.lower())
                    if key and key not in existing:
                        parts.append(qualifier)
                        existing += key
                return " ".join(parts), bucket
        except Exception as e:  # noqa: BLE001 - any failure means "guess instead"
            logger.debug("Could not read font name table, using filename",
                         path=path, error=str(e))
        return _split_family_and_style(stem)

    def _build_catalog(self) -> Dict[str, Dict[str, str]]:
        """
        Map ``{family: {style: path}}``.

        System directories are scanned first so that the drop-in directory,
        scanned last, overwrites any face it collides with.
        """
        catalog: Dict[str, Dict[str, str]] = {}

        def absorb(entries: List[Tuple[str, str]]) -> None:
            """
            Fold one source tier into the catalog.

            Within a tier the first face to claim a (family, style) slot keeps
            it, and entries are sorted by filename first, so two faces that
            genuinely collide resolve the same way on every scan rather than
            however ``os.walk`` happened to order them. Across tiers a later
            call still overwrites, which is what lets a drop-in face shadow a
            bundled one.
            """
            claimed = set()
            for stem, path in sorted(entries):
                family, style = self._face_identity(path, stem)
                # A leading dot marks a system-internal face (".SF Compact",
                # ".LastResort" on macOS). They are hidden from font pickers
                # everywhere else and are no use on a label either.
                if not family or family.startswith("."):
                    continue
                if (family, style) in claimed:
                    logger.debug("Ignoring duplicate face",
                                 family=family, style=style, path=path)
                    continue
                claimed.add((family, style))
                catalog.setdefault(family, {})[style] = path

        for directory in SYSTEM_FONT_DIRS:
            absorb(self._scan_dir(directory, recursive=False))
        absorb(self._scan_dir(self.user_font_dir, recursive=True))

        return catalog

    def _user_dir_stamp(self) -> Optional[Tuple[float, int]]:
        """
        A cheap fingerprint of the drop-in directory: (mtime, entry count).

        The count catches the case where a file is added and another removed
        within the same mtime granularity.
        """
        try:
            st = os.stat(self.user_font_dir)
        except OSError:
            return None
        count = 0
        for _root, _dirs, files in os.walk(self.user_font_dir):
            count += len(files)
        return (st.st_mtime, count)

    def _catalog_now(self) -> Dict[str, Dict[str, str]]:
        """Return the catalog, rebuilding it if the drop-in directory moved."""
        with self._lock:
            now = time.monotonic()
            if self._catalog is None:
                self._catalog = self._build_catalog()
                self._last_user_dir_stamp = self._user_dir_stamp()
                self._last_scan_at = now
                logger.info("Font catalog built",
                            families=len(self._catalog),
                            user_font_dir=self.user_font_dir)
            elif now - self._last_scan_at >= self._RESCAN_INTERVAL_SECONDS:
                self._last_scan_at = now
                stamp = self._user_dir_stamp()
                if stamp != self._last_user_dir_stamp:
                    self._catalog = self._build_catalog()
                    self._last_user_dir_stamp = stamp
                    logger.info("Font catalog rebuilt after drop-in change",
                                families=len(self._catalog),
                                user_font_dir=self.user_font_dir)
            return self._catalog

    def refresh(self) -> None:
        """Drop the cached catalog so the next read rescans from disk."""
        with self._lock:
            self._catalog = None
            self._last_user_dir_stamp = None

    # --- public API ------------------------------------------------------

    def list_families(self) -> List[Dict[str, Any]]:
        """
        The catalog as a JSON-friendly list, sorted by family name.

        Each entry carries the styles that family actually has, so the UI can
        grey out an italic that does not exist rather than silently substituting
        the regular face.
        """
        catalog = self._catalog_now()
        families = []
        for family in sorted(catalog):
            styles = catalog[family]
            families.append({
                "family": family,
                "styles": [s for s in STYLES if s in styles],
                "user_supplied": any(
                    self._is_user_supplied(p) for p in styles.values()
                ),
            })
        return families

    def _is_user_supplied(self, path: str) -> bool:
        try:
            return os.path.commonpath(
                [os.path.abspath(path), os.path.abspath(self.user_font_dir)]
            ) == os.path.abspath(self.user_font_dir)
        except ValueError:
            # Different drives on Windows; not a case this app runs in.
            return False

    def default_family(self) -> str:
        """
        The family used when none is configured.

        Prefers the historical DejaVu Sans so nothing changes for existing
        labels, then any family the catalog does have.
        """
        catalog = self._catalog_now()
        if DEFAULT_FAMILY in catalog:
            return DEFAULT_FAMILY
        for family in sorted(catalog):
            return family
        return DEFAULT_FAMILY

    def resolve(self, family: Optional[str] = None,
                style: Optional[str] = None) -> Optional[str]:
        """
        Find a font file for *family* in *style*.

        Falls back, in order: the requested style -> the family's regular face
        -> any face in that family -> the default family -> any face at all ->
        None (the caller then gets Pillow's built-in bitmap font).

        Family matching is case- and space-insensitive, so "dejavu sans" and
        "DejaVuSans" both find "DejaVu Sans".
        """
        catalog = self._catalog_now()
        if not catalog:
            return None

        if style not in STYLES:
            style = DEFAULT_STYLE

        styles = self._lookup_family(catalog, family) or {}
        if not styles:
            styles = self._lookup_family(catalog, self.default_family()) or {}
        if not styles:
            # Nothing matched by name; take whatever the catalog has.
            styles = catalog[sorted(catalog)[0]]

        return self._pick_style(styles, style)

    @staticmethod
    def _pick_style(styles: Dict[str, str], wanted: str) -> Optional[str]:
        """
        Choose the closest available style.

        A missing bold_italic degrades to bold, then italic, then regular --
        rather than to whatever happens to sort first, which on a family with
        only an oblique face would silently pick the slanted one for a request
        that asked for upright.
        """
        preference = {
            "bold_italic": ("bold_italic", "bold", "italic", "regular"),
            "bold": ("bold", "bold_italic", "regular", "italic"),
            "italic": ("italic", "bold_italic", "regular", "bold"),
            "regular": ("regular", "bold", "italic", "bold_italic"),
        }[wanted]
        for candidate in preference:
            if candidate in styles:
                return styles[candidate]
        return next(iter(styles.values()), None)

    @staticmethod
    def _lookup_family(catalog: Dict[str, Dict[str, str]],
                       family: Optional[str]) -> Optional[Dict[str, str]]:
        """Case- and whitespace-insensitive family lookup."""
        if not family:
            return None
        if family in catalog:
            return catalog[family]
        wanted = re.sub(r"[^a-z0-9]", "", family.lower())
        if not wanted:
            return None
        for known, styles in catalog.items():
            if re.sub(r"[^a-z0-9]", "", known.lower()) == wanted:
                return styles
        return None

    def path_for_id(self, font_id: str) -> Optional[str]:
        """
        Resolve a ``"<family>:<style>"`` identifier to a path.

        Used by the HTTP endpoint that serves font files to the browser so the
        client-side preview can render in the real typeface. Only ever returns
        paths already present in the catalog, so a crafted id cannot be used to
        read an arbitrary file.
        """
        if not font_id:
            return None
        family, _sep, style = font_id.rpartition(":")
        if not family:
            family, style = font_id, DEFAULT_STYLE
        catalog = self._catalog_now()
        styles = self._lookup_family(catalog, family)
        if not styles:
            return None
        return styles.get(style if style in STYLES else DEFAULT_STYLE)


def apply_text_font(combined: Dict[str, Any],
                    text_settings: Optional[Dict[str, Any]]) -> None:
    """
    Copy a text block's font choice into the keys the renderer reads.

    The composite layouts (QR + text, image + text) namespace their text options
    with a ``text_`` prefix so they do not collide with the label's own -- the
    existing ``text_font_size`` is the same idea. This mirrors that for family
    and style, in one place, because five controllers build the same mapping.

    Only truthy values are copied: an omitted font in the text block must fall
    through to the label-level setting, and writing an empty string would
    shadow it.

    Args:
        combined: The settings dict being assembled, modified in place.
        text_settings: The request's ``text`` block, if any.
    """
    for source_key, dest_key in (("font_family", "text_font_family"),
                                 ("font_style", "text_font_style")):
        value = (text_settings or {}).get(source_key)
        if value:
            combined[dest_key] = value


# Singleton, mirroring settings_service / printer_service.
font_service = FontService()
