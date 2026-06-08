# knowco2/web/i18n/__init__.py
# ------------------------------------------------------------------
# Internationalisation helpers for the settings portal.
#
# LANG_NAMES  – ordered dict of language code → display name shown in
#               the selector.  Add a new entry here + a matching <code>.py
#               file in this directory to support an additional language.
#
# build_lang_options(current_lang) – renders the <option> elements for
#               the language <select>.
#
# build_translations_js()  – builds the "var T={...};" JS block that
#               portal_page.py inlines into the settings page script.
# ------------------------------------------------------------------

import json

LANG_NAMES = {
    "en": "English",
    "es": "Español",
    "fr": "Français",
    "de": "Deutsch",
    "pt": "Português",
    "it": "Italiano",
    "nl": "Nederlands",
    "sv": "Svenska",
    "pl": "Polski",
    "cs": "Čeština",
    "ru": "Русский",
    "uk": "Українська",
    "tr": "Türkçe",
    "vi": "Tiếng Việt",
    "id": "Bahasa Indonesia",
    "ja": "日本語",
    "zh": "中文（简体）",
    "ko": "한국어",
}


def build_lang_options(current_lang):
    """Return HTML <option> elements for the language <select>."""
    opts = []
    for code, name in LANG_NAMES.items():
        sel = " selected" if code == current_lang else ""
        opts.append("<option value='" + code + "'" + sel + ">" + name + "</option>")
    return "\n            ".join(opts)


def build_translations_js():
    """Return the var T={...}; JS block built from per-language modules.

    Each language module must export a dict named T containing all i18n keys.
    New languages: add an entry to LANG_NAMES above and create a matching
    <code>.py file next to this file.
    """
    from . import en, es, fr, de, pt, it, nl, sv, pl, cs, ru, uk, tr, vi, id, ja, zh, ko
    parts = []
    for code, mod in (
        ("en", en), ("es", es), ("fr", fr), ("de", de), ("pt", pt),
        ("it", it), ("nl", nl), ("sv", sv), ("pl", pl), ("cs", cs),
        ("ru", ru), ("uk", uk), ("tr", tr), ("vi", vi), ("id", id),
        ("ja", ja), ("zh", zh), ("ko", ko),
    ):
        parts.append('"' + code + '":' + json.dumps(mod.T))
    return "var T={" + ",".join(parts) + "};"
