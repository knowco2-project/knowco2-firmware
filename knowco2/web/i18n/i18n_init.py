# knowco2/web/i18n/__init__.py
# ------------------------------------------------------------------
# Internationalisation helpers for the settings portal.
#
# LANG_NAMES  – ordered dict of language code → display name shown in
#               the selector.  Add a new entry here + a matching entry
#               in translations.py's TRANSLATIONS dict to support an
#               additional language.
#
# build_lang_options(current_lang) – renders the <option> elements for
#               the language <select>.
#
# build_translations_js()  – builds the "var T={...};" JS block that
#               portal_page.py inlines into the settings page script.
# ------------------------------------------------------------------

import json

from .translations import TRANSLATIONS

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
    "hi": "हिन्दी",
    "bn": "বাংলা",
    "ta": "தமிழ்",
    "th": "ภาษาไทย",
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
    """Return the var T={...}; JS block built from TRANSLATIONS.

    New languages: add an entry to LANG_NAMES above and to the
    TRANSLATIONS dict in translations.py.
    """
    parts = []
    for code in LANG_NAMES:
        parts.append('"' + code + '":' + json.dumps(TRANSLATIONS[code]))
    return "var T={" + ",".join(parts) + "};"
