# knowco2/languages.py
# ----------------------------------------------------------------------
# Language-code catalog shared by settings persistence and the web portal.
# Keep this module data-only: settings.py imports it during normal boot, so
# it must remain small and must not import the much larger translation pack.
# ----------------------------------------------------------------------

BASE_LANGUAGE_CODES = (
    "en", "es", "fr", "de", "pt", "it", "nl", "sv", "pl", "cs",
    "ru", "uk", "tr", "vi", "id", "hi", "bn", "ta", "th", "ja",
    "zh", "ko",
)

EXTRA_LANGUAGE_CODES = (
    "da", "nb", "fi", "ro", "hu", "sk", "el", "bg", "hr", "sl",
    "ca", "pt-PT", "ar", "he", "ms", "fil", "zh-TW", "af", "sw", "et",
    "fa", "ur", "mr", "te", "gu", "kn", "ml", "ne",
)

SUPPORTED_LANGUAGE_CODES = BASE_LANGUAGE_CODES + EXTRA_LANGUAGE_CODES

# Browser portal only. The on-device TFT remains English because the
# built-in bitmap font does not contain these scripts.
RTL_LANGUAGE_CODES = ("ar", "he", "fa", "ur")
