"""Validate the 50-language portal catalog, packs, literals, and size budget."""

import importlib.util
import io
import json
from pathlib import Path
import re
import sys
import types
import zlib

ROOT = Path(__file__).resolve().parents[1]
KC2 = ROOT / "knowco2"
I18N = KC2 / "web" / "i18n"
PACKS = [I18N / ("translations_extra_%d.pack" % n) for n in range(1, 6)]


def load(name, path, package=False):
    spec = importlib.util.spec_from_file_location(
        name, path, submodule_search_locations=[str(path.parent)] if package else None)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Import only localization data, not hardware-dependent web modules.
pkg = types.ModuleType("knowco2")
pkg.__path__ = [str(KC2)]
sys.modules["knowco2"] = pkg
languages = load("knowco2.languages", KC2 / "languages.py")
web = types.ModuleType("knowco2.web")
web.__path__ = [str(KC2 / "web")]
sys.modules["knowco2.web"] = web
translations = load("knowco2.web.i18n.translations", I18N / "translations.py")
i18n = load("knowco2.web.i18n", I18N / "__init__.py", package=True)

english = translations.TRANSLATIONS["en"]
keys = list(english)
supported = languages.SUPPORTED_LANGUAGE_CODES
assert len(supported) == len(set(supported)) == 50
assert tuple(i18n.LANG_NAMES) == supported
for code in languages.BASE_LANGUAGE_CODES:
    assert set(translations.TRANSLATIONS[code]) == set(keys), code

# Verify each index, zlib stream, locale row, and the full FAT-aware budget.
extra = {}
for path in PACKS:
    source = io.BytesIO(path.read_bytes())
    assert source.readline() == b"KC2I18N1\n", path.name
    entries = []
    while True:
        line = source.readline()
        if line in (b"\n", b"\r\n"):
            break
        assert line, path.name
        fields = line.rstrip(b"\r\n").split(b"\t")
        assert len(fields) == 4, line
        entries.append((fields[0].decode("ascii"), *(int(v) for v in fields[1:])))
    start = source.tell()
    expected_offset = 0
    for code, offset, compressed_size, raw_size in entries:
        assert code not in extra and offset == expected_offset, code
        source.seek(start + offset)
        raw = zlib.decompress(source.read(compressed_size))
        assert len(raw) == raw_size <= 8 * 1024, code
        assert compressed_size <= 2 * 1024, code
        row = json.loads(raw.decode("utf-8"))
        assert len(row) == len(keys), code
        for value in row:
            assert value is None or (isinstance(value, str) and value.strip()), code
            if isinstance(value, str):
                assert not re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", value), code
        extra[code] = row
        expected_offset += compressed_size
    assert start + expected_offset == path.stat().st_size, path.name

assert set(extra) == set(languages.EXTRA_LANGUAGE_CODES)
assert sum(path.stat().st_size for path in PACKS) <= 44 * 1024
allocated = sum(((path.stat().st_size + 4095) // 4096) * 4096 for path in PACKS)
assert allocated <= 52 * 1024
for code in languages.EXTRA_LANGUAGE_CODES:
    assert i18n._load_extra_values(code, len(keys)) == extra[code], code

# Validate portal keys and technical strings across all 50 complete tables.
portal = (KC2 / "web" / "portal_page.py").read_text(encoding="utf-8")
used = set(re.findall(r'''data-i18n(?:-placeholder|-aria)?=["']([^"']+)["']''', portal))
assert not (used - set(english))
exact = {
    "ph_cloud_url": "https://api.knowco2.com/v1/ingest",
    "ph_mqtt_prefix": "knowco2",
    "ph_aio_group": "knowco2",
    "ph_fw_url": "http://192.168.1.x/firmware.py",
}
co2_keys = [key for key, value in english.items() if "CO₂" in value]
ppm_keys = [key for key, value in english.items() if "ppm" in value]
for code in supported:
    if code in translations.TRANSLATIONS:
        table = translations.TRANSLATIONS[code]
    else:
        table = {key: english[key] if value is None else value
                 for key, value in zip(keys, extra[code])}
    for key, value in exact.items():
        assert table[key] == value, (code, key)
    assert "192.168.1.x" in table["ph_mqtt_broker"] and "mqtt." in table["ph_mqtt_broker"]
    assert "180°" in table["help_flip"]
    assert all("CO₂" in table[key] for key in co2_keys), code
    assert all("ppm" in table[key] for key in ppm_keys), code

# The browser gets the 22 base locales plus at most the selected extra locale.
def packed(script):
    match = re.search(r"var _V=(\{.*?\});\(function", script)
    assert match
    return json.loads(match.group(1))

base = set(languages.BASE_LANGUAGE_CODES)
assert set(packed(i18n.build_translations_js("en"))) == base
persian = i18n.build_translations_js("fa")
assert set(packed(persian)) == base | {"fa"}
assert '"ur":' not in re.search(r"var _V=.*?;\(function", persian).group(0)
assert "this.form.submit()" not in persian
assert "var _RTL=" in persian and "var _KCO2_CURRENT_LANG=" in persian
options = i18n.build_lang_options("fa")
assert "value='fa' dir='rtl' selected" in options
for code in languages.RTL_LANGUAGE_CODES:
    assert re.search(r"value='%s' dir='rtl'" % code, options)

# Extra-language reloads must use a separate allow-listed form, and stale
# browser-local locales must fall back to a locale actually present in T.
assert "function submitLanguageOnly(lang, sourceForm)" in portal
assert "addField('lang_only', '1')" in portal
assert "addField('lang', lang)" in portal
assert "input[name=pw]" in portal
assert "if (!T[saved])" in portal
assert "if (!T[lang])" in portal
assert "localStorage.getItem('kco2_lang') || _KCO2_CURRENT_LANG" in portal
assert "submitLanguageOnly(requested, this.form)" in portal
assert "this.form.submit()" not in portal

# The server-side marker is an allow-listed fast path. Even a malformed locale
# request carrying unrelated fields must change only the language.
storage = types.ModuleType("storage")
storage.remount = lambda *args, **kwargs: None
storage.getmount = lambda *args, **kwargs: types.SimpleNamespace(readonly=False)
sys.modules["storage"] = storage
settings = load("knowco2.settings", KC2 / "settings.py")
settings.save_settings = lambda: True
settings.state.settings.clear()
settings.state.settings.update({
    "lang": "en",
    "ap_ssid": "knowco2-safe",
    "cloud_enabled": True,
    "mqtt_enabled": True,
    "display_flip": True,
})
before = dict(settings.state.settings)
changed_ap = settings.update_settings_from_params({
    "lang_only": "1",
    "lang": "fa",
    "ap_ssid": "should-not-apply",
    "cloud_enabled": "",
})
assert changed_ap is False
assert settings.state.settings == dict(before, lang="fa")

settings.update_settings_from_params({"lang_only": "1", "lang": "invalid"})
assert settings.state.settings == dict(before, lang="fa")

legacy = "var T=" + json.dumps(
    {code: translations.TRANSLATIONS[code] for code in languages.BASE_LANGUAGE_CODES},
    ensure_ascii=False, separators=(",", ":"))
assert len(i18n.build_translations_js("en").encode()) < len(legacy.encode())
print("Localization validation passed: 50 languages, %d packed bytes." %
      sum(path.stat().st_size for path in PACKS))
