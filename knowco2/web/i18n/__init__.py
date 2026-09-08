# knowco2/web/i18n/__init__.py
# ------------------------------------------------------------------
# Internationalisation helpers for the settings portal.
#
# The original 22-language pack remains in translations.py so existing
# languages switch instantly in the browser. Additional languages are kept
# in five indexed, zlib-compressed pack files and only the currently selected
# extra language is decompressed. This bounds response size and avoids one
# FAT allocation cluster per individual language.
# ------------------------------------------------------------------

import json

from ...languages import (
    BASE_LANGUAGE_CODES,
    EXTRA_LANGUAGE_CODES,
    RTL_LANGUAGE_CODES,
    SUPPORTED_LANGUAGE_CODES,
)
from .translations import TRANSLATIONS


LANG_NAMES = {
    "en": "English",
    "es": "Español",
    "fr": "Français",
    "de": "Deutsch",
    "pt": "Português (Brasil)",
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
    "da": "Dansk",
    "nb": "Norsk bokmål",
    "fi": "Suomi",
    "ro": "Română",
    "hu": "Magyar",
    "sk": "Slovenčina",
    "el": "Ελληνικά",
    "bg": "Български",
    "hr": "Hrvatski",
    "sl": "Slovenščina",
    "ca": "Català",
    "pt-PT": "Português (Portugal)",
    "ar": "العربية",
    "he": "עברית",
    "ms": "Bahasa Melayu",
    "fil": "Filipino",
    "zh-TW": "中文（繁體）",
    "af": "Afrikaans",
    "sw": "Kiswahili",
    "et": "Eesti",
    "fa": "فارسی",
    "ur": "اردو",
    "mr": "मराठी",
    "te": "తెలుగు",
    "gu": "ગુજરાતી",
    "kn": "ಕನ್ನಡ",
    "ml": "മലയാളം",
    "ne": "नेपाली",
}


# High-confidence corrections found during the 2026-09 localization audit.
# Overrides keep the large legacy file stable and make corrections easy to
# review. Only keys present in English are applied, which also keeps the
# lightweight onboarding test stub compatible.
_BASE_CORRECTIONS = {
    "nl": {
        "help_flip": (
            "Draait het scherm 180° zodat het correct leesbaar is bij "
            "omgekeerde montage. De knoppen worden niet beïnvloed."
        ),
    },
    "sv": {
        "sec_mqtt": "MQTT-broker",
        "lbl_mqtt_broker": "Brokerns värdnamn/IP",
        "help_mqtt": (
            "Publicera avläsningar till en lokal MQTT-broker "
            "(t.ex. Home Assistant)."
        ),
        "lbl_scale_fixed": "400–2000 ppm (snäv)",
        "lbl_max_pts": "Historikbuffert (mätvärden)",
        "lbl_mqtt_user": "Användarnamn (valfritt)",
        "lbl_mqtt_pass": "Lösenord (valfritt)",
    },
    "pl": {
        "sec_cloud": "Wysyłanie do chmury",
        "lbl_med": "Średni próg (ppm)",
        "lbl_cloud_en": "Włącz wysyłanie do chmury",
        "help_flip": (
            "Obraca ekran o 180°, aby był czytelny przy montażu do góry "
            "nogami. Przyciski pozostają bez zmian."
        ),
    },
    "cs": {
        "lbl_flip": "Otočit displej (montáž vzhůru nohama)",
        "help_flip": (
            "Otočí obrazovku o 180°, aby byla čitelná při montáži vzhůru "
            "nohama. Tlačítka nejsou ovlivněna."
        ),
    },
    "ru": {
        "lbl_max_pts": "Буфер истории (измерений)",
    },
    "uk": {
        "lbl_max_pts": "Буфер історії (вимірювань)",
    },
    "tr": {
        "ph_sta_pass": "Wi-Fi şifreniz",
        "lbl_flip": "Ekranı çevir (baş aşağı montaj)",
    },
    "ja": {
        "help_low": "CO₂レベルがこの値未満の場合は緑色で表示されます。",
        "help_med": "CO₂レベルがこの値未満の場合は黄色で表示されます。",
        "lbl_mode_big": "大きな CO₂",
        "lbl_scale_fixed": "400–2000 ppm（狭い範囲）",
        "ph_mqtt_broker": "192.168.1.x または mqtt.example.com",
    },
    "zh": {
        "sec_cloud": "云端上传",
        "sec_mqtt": "MQTT 代理服务器",
        "lbl_mqtt_broker": "代理服务器主机名/IP",
        "help_mqtt": (
            "将读数发布到本地 MQTT 代理服务器（例如 Home Assistant）。"
        ),
        "ph_mqtt_broker": "192.168.1.x 或 mqtt.example.com",
    },
    "ko": {
        "help_low": "CO₂ 수준이 이 값 미만이면 녹색으로 표시됩니다.",
        "help_med": "CO₂ 수준이 이 값 미만이면 노란색으로 표시됩니다.",
        "lbl_scale_fixed": "400–2000 ppm (좁은 범위)",
        "ph_mqtt_broker": "192.168.1.x 또는 mqtt.example.com",
    },
}


def _apply_base_corrections():
    english = TRANSLATIONS.get("en", {})
    for code, changes in _BASE_CORRECTIONS.items():
        table = TRANSLATIONS.get(code)
        if not table:
            continue
        for key, value in changes.items():
            if key in english:
                table[key] = value


_apply_base_corrections()


def _compact_json(value):
    """Use compact UTF-8 JSON where supported; fall back to tiny json APIs."""
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except TypeError:
        try:
            return json.dumps(value, separators=(",", ":"))
        except TypeError:
            return json.dumps(value)


def _extra_translation_paths():
    names = (
        "translations_extra_1.pack",
        "translations_extra_2.pack",
        "translations_extra_3.pack",
        "translations_extra_4.pack",
        "translations_extra_5.pack",
    )
    paths = []
    for name in names:
        paths.append("/knowco2/web/i18n/" + name)
    try:
        module_dir = __file__.rsplit("/", 1)[0]
        for name in names:
            local_path = module_dir + "/" + name
            if local_path not in paths:
                paths.append(local_path)
    except Exception:
        pass
    return paths


def _load_extra_values(code, expected_count):
    """Read and decompress one indexed extra-language value array."""
    if code not in EXTRA_LANGUAGE_CODES:
        return None

    try:
        import zlib
    except ImportError:
        return None

    wanted = code.encode("ascii")
    for path in _extra_translation_paths():
        try:
            with open(path, "rb") as source:
                if source.readline() != b"KC2I18N1\n":
                    continue

                target_offset = None
                compressed_size = None
                raw_size = None
                data_start = None

                while True:
                    line = source.readline()
                    if not line:
                        break
                    if line in (b"\n", b"\r\n"):
                        data_start = source.tell()
                        break
                    fields = line.rstrip(b"\r\n").split(b"\t")
                    if len(fields) != 4 or fields[0] != wanted:
                        continue
                    target_offset = int(fields[1])
                    compressed_size = int(fields[2])
                    raw_size = int(fields[3])

                if data_start is None or target_offset is None:
                    continue
                if compressed_size is None or raw_size is None:
                    continue
                if compressed_size < 1 or raw_size < 1:
                    continue

                source.seek(data_start + target_offset)
                compressed = source.read(compressed_size)
                if len(compressed) != compressed_size:
                    continue
                raw = zlib.decompress(compressed)
                del compressed
                if len(raw) != raw_size:
                    continue
                values = json.loads(raw.decode("utf-8"))
                del raw

                if not isinstance(values, list):
                    return None
                if len(values) != expected_count:
                    return None
                return values
        except (OSError, ValueError):
            continue
        except Exception:
            continue
    return None


def _current_language():
    try:
        from ... import state
        code = state.settings.get("lang", "en")
        if code in SUPPORTED_LANGUAGE_CODES:
            return code
    except Exception:
        pass
    return "en"


def build_lang_options(current_lang):
    """Return HTML <option> elements for the language selector."""
    opts = []
    for code in SUPPORTED_LANGUAGE_CODES:
        name = LANG_NAMES[code]
        sel = " selected" if code == current_lang else ""
        direction = " dir='rtl'" if code in RTL_LANGUAGE_CODES else ""
        opts.append(
            "<option value='" + code + "'" + direction + sel + ">" +
            name + "</option>"
        )
    return "\n            ".join(opts)


def _delta_values(table, keys, english_values):
    values = []
    for index, key in enumerate(keys):
        value = table.get(key, english_values[index])
        values.append(None if value == english_values[index] else value)
    return values


def build_translations_js(current_lang=None):
    """Return compact JavaScript translations for the settings page.

    All original/base languages are included so their selector changes remain
    instant. At most one extra language (the current saved language) is added.
    The portal page performs a language-only reload when another extra language
    is selected.
    """
    if current_lang is None:
        current_lang = _current_language()
    if current_lang not in SUPPORTED_LANGUAGE_CODES:
        current_lang = "en"

    english = TRANSLATIONS.get("en", {})
    keys = list(english.keys())
    english_values = [english[key] for key in keys]

    packed = {"en": english_values}
    for code in BASE_LANGUAGE_CODES:
        if code == "en":
            continue
        table = TRANSLATIONS.get(code, {})
        packed[code] = _delta_values(table, keys, english_values)

    if current_lang in EXTRA_LANGUAGE_CODES:
        extra = _load_extra_values(current_lang, len(keys))
        if extra is not None:
            packed[current_lang] = extra

    rtl = {}
    for code in RTL_LANGUAGE_CODES:
        rtl[code] = 1

    extra_codes = {}
    for code in EXTRA_LANGUAGE_CODES:
        extra_codes[code] = 1

    # Start with "var T={" for compatibility with the existing onboarding
    # regression test and with portal_page.py's applyLang() function.
    return (
        "var T={};"
        "var _K=" + _compact_json(keys) + ";"
        "var _V=" + _compact_json(packed) + ";"
        "(function(){var e=_V.en;for(var c in _V){"
        "if(!_V.hasOwnProperty(c))continue;"
        "var a=_V[c],o={};for(var i=0;i<_K.length;i++){"
        "o[_K[i]]=a[i]===null?e[i]:a[i];}T[c]=o;}})();"
        "var _RTL=" + _compact_json(rtl) + ";"
        "var _EXTRA=" + _compact_json(extra_codes) + ";"
        "var _KCO2_CURRENT_LANG=" + _compact_json(current_lang) + ";"
        "function _kco2Dir(c){document.documentElement.dir="
        "_RTL[c]?'rtl':'ltr';}"
    )
