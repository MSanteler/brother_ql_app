// Brother QL Printer App - Font catalog and preview font loading
//
// Two jobs:
//
//   1. Fill the typeface/style pickers from GET /api/v1/fonts, so the choices
//      offered are the faces actually installed rather than a hardcoded list
//      that drifts from the image.
//
//   2. Load those same faces into the browser so the client-side draft preview
//      renders in the typeface the printer will use. Without this the draft
//      shows the browser's default sans and silently disagrees with the print
//      -- which is exactly the kind of preview/print mismatch the server render
//      exists to catch.

/**
 * The catalog as returned by the API, plus the pickers wired to it.
 * `families` stays empty until loadFontCatalog() succeeds; every consumer
 * treats that as "no choice offered" rather than an error.
 */
const fontCatalog = {
    families: [],
    defaultFamily: '',
    styles: ['regular', 'bold', 'italic', 'bold_italic'],
    userFontDir: ''
};

/** Human labels for the style values, which are wire format, not display text. */
const FONT_STYLE_LABELS = {
    regular: 'Regular',
    bold: 'Bold',
    italic: 'Italic',
    bold_italic: 'Bold Italic'
};

/**
 * The typeface/style select pairs in the compose forms. The text pair doubles
 * as the saved default, exactly as text-font-size and text-alignment already
 * do -- see handleSaveSettings in api.js.
 */
const FONT_PICKERS = [
    { family: 'text-font-family', style: 'text-font-style' },
    { family: 'qr-text-font-family', style: 'qr-text-font-style' },
    { family: 'label-text-font-family', style: 'label-text-font-style' },
    { family: 'textimage-font-family', style: 'textimage-font-style' }
];

/**
 * Faces already handed to the browser, keyed "family:style".
 * Values are a promise resolving to the CSS family name, or to null when the
 * face could not be loaded. Cached either way: a font that 404s once will 404
 * again, and retrying on every keystroke would hammer the endpoint.
 */
const loadedFontFaces = new Map();

/**
 * Fetch the catalog and fill every picker.
 *
 * Failure is not fatal and not worth a toast: the pickers keep whatever they
 * have (the default entry), and printing still works because the server falls
 * back to the default face when no family is named.
 */
async function loadFontCatalog() {
    try {
        const response = await fetch('/api/v1/fonts');
        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        const data = await response.json();
        fontCatalog.families = Array.isArray(data.fonts) ? data.fonts : [];
        fontCatalog.defaultFamily = data.default_family || '';
        fontCatalog.userFontDir = data.user_font_dir || '';
        if (Array.isArray(data.styles) && data.styles.length) {
            fontCatalog.styles = data.styles;
        }

        FONT_PICKERS.forEach(pair => {
            const familySelect = document.getElementById(pair.family);
            const styleSelect = document.getElementById(pair.style);
            if (!familySelect) return;

            populateFamilySelect(familySelect);
            syncStyleOptions(familySelect, styleSelect);

            // Re-limit the styles whenever the family changes: a family with no
            // italic must not leave "Italic" selected and silently print
            // upright.
            familySelect.addEventListener('change', () => {
                syncStyleOptions(familySelect, styleSelect);
            });
        });

        applyUserFontDirHint();
        console.log(`Font catalog loaded: ${fontCatalog.families.length} families`);
    } catch (error) {
        console.error('Error loading font catalog:', error);
    }
}

/**
 * Fill one family <select>, preserving whatever was already chosen.
 *
 * Faces from the drop-in directory go in their own group so it is obvious
 * which ones the user added -- and, when that group is empty, that the feature
 * exists at all.
 * @param {HTMLSelectElement} select
 */
function populateFamilySelect(select) {
    // dataset.desiredFamily, not select.value: the saved settings and the
    // catalog are fetched independently, and whichever lands first must not
    // beat the other. Assigning a <select>.value with no matching <option>
    // silently leaves it unchanged, so if the settings arrive first the saved
    // family would be dropped here. Remembering the *wanted* value makes the
    // two orderings equivalent.
    const previous = select.dataset.desiredFamily != null
        ? select.dataset.desiredFamily
        : select.value;
    select.innerHTML = '';

    // An explicit "default" entry rather than a preselected family: it stores
    // an empty string, which is what tells the server "use the configured
    // default" instead of pinning this label to a family by name.
    const defaultOption = document.createElement('option');
    defaultOption.value = '';
    defaultOption.textContent = fontCatalog.defaultFamily
        ? `Default (${fontCatalog.defaultFamily})`
        : 'Default';
    select.appendChild(defaultOption);

    const bundled = fontCatalog.families.filter(f => !f.user_supplied);
    const userSupplied = fontCatalog.families.filter(f => f.user_supplied);

    appendFamilyGroup(select, 'Installed', bundled);
    appendFamilyGroup(select, 'Your fonts', userSupplied);

    // Restore the previous choice if it still exists; otherwise fall back to
    // the default entry rather than silently landing on whatever sorts first.
    select.value = previous;
    if (select.selectedIndex === -1) select.value = '';
}

/**
 * Append one <optgroup> of families, skipping the group entirely when empty.
 * @param {HTMLSelectElement} select
 * @param {string} label
 * @param {Array<object>} families
 */
function appendFamilyGroup(select, label, families) {
    if (!families.length) return;

    const group = document.createElement('optgroup');
    group.label = label;
    families.forEach(font => {
        const option = document.createElement('option');
        option.value = font.family;
        option.textContent = font.family;
        // Read back by syncStyleOptions without another catalog lookup.
        option.dataset.styles = (font.styles || []).join(',');
        group.appendChild(option);
    });
    select.appendChild(group);
}

/**
 * Disable the styles the selected family does not provide.
 *
 * The server degrades a missing style to the closest available one, so this is
 * about honesty rather than correctness: offering "Italic" for a family that
 * has none produces an upright label and looks like a bug.
 * @param {HTMLSelectElement} familySelect
 * @param {HTMLSelectElement} styleSelect
 */
function syncStyleOptions(familySelect, styleSelect) {
    if (!styleSelect) return;

    const selected = familySelect.options[familySelect.selectedIndex];
    const available = selected && selected.dataset.styles
        ? selected.dataset.styles.split(',').filter(Boolean)
        : null;

    Array.from(styleSelect.options).forEach(option => {
        // No family chosen (the "Default" entry) means we cannot know which
        // styles exist, so leave them all enabled.
        option.disabled = !!available && !available.includes(option.value);
    });

    // If the current choice just became unavailable, move to one that exists.
    const current = styleSelect.options[styleSelect.selectedIndex];
    if (current && current.disabled) {
        const fallback = Array.from(styleSelect.options).find(o => !o.disabled);
        if (fallback) styleSelect.value = fallback.value;
    }
}

/**
 * Show where to drop custom fonts, if the page has somewhere to say it.
 */
function applyUserFontDirHint() {
    const hint = document.getElementById('font-dropin-hint');
    if (hint && fontCatalog.userFontDir) {
        hint.textContent = `Drop .ttf or .otf files into ${fontCatalog.userFontDir} `
            + 'to add your own — they appear here within a few seconds.';
    }
}

/**
 * Select a family, remembering the choice even if the catalog has not arrived.
 *
 * Pair with populateFamilySelect, which replays it once the options exist.
 * @param {string} selectId
 * @param {string} family - Empty string means "use the default".
 */
function setFontFamilyValue(selectId, family) {
    const select = document.getElementById(selectId);
    if (!select) return;

    select.dataset.desiredFamily = family || '';
    select.value = family || '';
    // The family may not be in the catalog yet (or at all, if it was
    // uninstalled); fall back to the default entry rather than leaving whatever
    // was selected before.
    if (select.selectedIndex === -1) select.value = '';
}

/**
 * Read one compose form's font choice.
 * @param {string} familyId
 * @param {string} styleId
 * @returns {{family: string, style: string}} Empty strings mean "inherit".
 */
function readFontChoice(familyId, styleId) {
    const familySelect = document.getElementById(familyId);
    const styleSelect = document.getElementById(styleId);
    return {
        family: familySelect ? familySelect.value : '',
        style: styleSelect ? styleSelect.value : ''
    };
}

/**
 * A CSS-safe, collision-free family name for one face.
 * @param {string} family
 * @param {string} style
 * @returns {string}
 */
function previewFontName(family, style) {
    const slug = `${family}-${style}`.replace(/[^a-zA-Z0-9]+/g, '-').toLowerCase();
    return `bqlf-${slug}`;
}

/**
 * Load one face into the document so the draft preview can use it.
 *
 * Each (family, style) is registered under its own CSS family name carrying the
 * real face file, and applied at weight/style normal. Registering the bold file
 * as "bold" instead would let the browser stack a synthetic emboldening on top
 * of an already-bold face, which is not what the printer does.
 *
 * @param {string} family
 * @param {string} style
 * @returns {Promise<string|null>} CSS family name, or null if unavailable.
 */
function ensureFontFace(family, style) {
    if (!family) return Promise.resolve(null);

    const effectiveStyle = style || 'regular';
    const key = `${family}:${effectiveStyle}`;
    if (loadedFontFaces.has(key)) return loadedFontFaces.get(key);

    // FontFace is unavailable in older browsers and in some embedded webviews;
    // the preview simply keeps its fallback stack there.
    if (typeof FontFace === 'undefined' || !document.fonts) {
        const unsupported = Promise.resolve(null);
        loadedFontFaces.set(key, unsupported);
        return unsupported;
    }

    const cssName = previewFontName(family, effectiveStyle);
    const url = `/api/v1/fonts/${encodeURIComponent(key)}/file`;

    // Fetched by hand rather than handed to FontFace as a url() source.
    //
    // Font loads triggered from CSS or from `new FontFace(name, 'url(...)')`
    // are made in anonymous CORS mode, which sends NO cookies -- so behind the
    // OIDC session login every preview font would come back 401 and silently
    // fall back. A same-origin fetch() sends the session cookie by default, and
    // FontFace accepts the raw bytes just as happily.
    const loading = fetch(url)
        .then(response => {
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return response.arrayBuffer();
        })
        .then(buffer => new FontFace(cssName, buffer).load())
        .then(face => {
            document.fonts.add(face);
            return cssName;
        })
        .catch(error => {
            // Expected in the static demo, where the API is stubbed and serves
            // no font files. Not worth surfacing to the user: the draft just
            // renders in the fallback face and the server render is unaffected.
            console.warn(`Could not load preview font ${key}:`, error);
            return null;
        });

    loadedFontFaces.set(key, loading);
    return loading;
}

/**
 * Render an element's text in the chosen face.
 *
 * Applies a fallback stack immediately and upgrades to the real face once it
 * has loaded, so a first-time font switch does not blank the preview while the
 * file is in flight.
 *
 * @param {HTMLElement} element
 * @param {string} family
 * @param {string} style
 */
function applyPreviewFont(element, family, style) {
    if (!element) return;

    const effectiveStyle = style || 'regular';
    // The bundled default is DejaVu Sans Bold, so a generic sans-serif at bold
    // is the closest the browser can do unaided.
    const fallback = 'system-ui, sans-serif';
    element.style.fontWeight = 'normal';
    element.style.fontStyle = 'normal';

    if (!family) {
        // No family chosen: the server uses its configured default, which we
        // cannot name here. Approximate it from the style alone.
        element.style.fontFamily = fallback;
        element.style.fontWeight = effectiveStyle.includes('bold') ? 'bold' : 'normal';
        element.style.fontStyle = effectiveStyle.includes('italic') ? 'italic' : 'normal';
        return;
    }

    element.style.fontFamily = fallback;
    element.style.fontWeight = effectiveStyle.includes('bold') ? 'bold' : 'normal';
    element.style.fontStyle = effectiveStyle.includes('italic') ? 'italic' : 'normal';

    ensureFontFace(family, effectiveStyle).then(cssName => {
        if (!cssName) return;
        // The face file already carries the weight and slant, so drop the
        // synthetic ones now that the real glyphs are available.
        element.style.fontFamily = `"${cssName}", ${fallback}`;
        element.style.fontWeight = 'normal';
        element.style.fontStyle = 'normal';
    });
}
