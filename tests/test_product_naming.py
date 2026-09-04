"""Production naming and service-volume regression tests."""

import re
import runpy
from pathlib import Path
import sys
import types
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


def _module(name, **attributes):
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    return module


class _Display:
    rotation = 0
    root_group = None


class _DigitalInOut:
    held = False
    pins = []

    def __init__(self, pin):
        self.pin = pin
        self.value = self.held
        self.deinitialized = False
        self.pins.append(pin)

    def switch_to_input(self, pull=None):
        self.pull = pull

    def deinit(self):
        self.deinitialized = True


class _Mount:
    def __init__(self):
        self.label = "CIRCUITPY"
        self.readonly = True


class _Storage(types.ModuleType):
    def __init__(self):
        super().__init__("storage")
        self.mount = _Mount()
        self.remounts = []
        self.usb_disabled = False

    def remount(self, path, readonly=False):
        self.remounts.append((path, readonly))
        self.mount.readonly = readonly

    def getmount(self, path):
        if path != "/":
            raise AssertionError("unexpected mount path")
        return self.mount

    def disable_usb_drive(self):
        self.usb_disabled = True


class ProductNamingTests(unittest.TestCase):
    def _run_boot(self, button_b_held):
        storage = _Storage()
        _DigitalInOut.held = button_b_held
        _DigitalInOut.pins = []

        replacements = {
            "board": _module("board", D1="board.D1", DISPLAY=_Display()),
            "displayio": _module("displayio", Group=lambda: object()),
            "digitalio": _module(
                "digitalio",
                DigitalInOut=_DigitalInOut,
                Pull=types.SimpleNamespace(DOWN="down"),
            ),
            "storage": storage,
            "usb_hid": _module("usb_hid", disable=lambda: None),
            "usb_midi": _module("usb_midi", disable=lambda: None),
        }
        previous = {name: sys.modules.get(name) for name in replacements}
        sys.modules.update(replacements)
        try:
            namespace = runpy.run_path(str(REPO_ROOT / "boot.py"))
        finally:
            for name, old_module in previous.items():
                if old_module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = old_module
        return namespace, storage

    def test_customer_boot_hides_named_service_volume(self):
        namespace, storage = self._run_boot(button_b_held=False)
        self.assertEqual(namespace["SERVICE_VOLUME_LABEL"], "KNOWCO2")
        self.assertEqual(_DigitalInOut.pins, ["board.D1"])
        self.assertEqual(storage.mount.label, "KNOWCO2")
        self.assertEqual(storage.remounts, [("/", False)])
        self.assertTrue(storage.usb_disabled)

    def test_button_b_boot_exposes_named_writable_service_volume(self):
        _, storage = self._run_boot(button_b_held=True)
        self.assertEqual(storage.mount.label, "KNOWCO2")
        self.assertEqual(storage.remounts, [("/", False), ("/", True)])
        self.assertFalse(storage.usb_disabled)

    def test_browser_never_exposes_internal_pin_or_old_brand_names(self):
        browser_sources = (
            "knowco2/web/portal_page.py",
            "knowco2/web/routes.py",
            "knowco2/web/i18n/translations.py",
        )
        for relative_path in browser_sources:
            content = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
            with self.subTest(path=relative_path):
                self.assertIsNone(re.search(r"\bD[012]\b", content))
                self.assertNotIn("CIRCUITPY", content)
                self.assertNotIn("Know CO2", content)
                self.assertNotIn("Know CO₂", content)

    def test_raw_pin_names_are_limited_to_low_level_sources(self):
        allowed = {
            "PRODUCT_NAMING.md",
            "README.md",
            "boot.py",
            "code.py",
            "knowco2/buttons.py",
            "tests/test_buttons.py",
            "tests/test_product_naming.py",
        }
        offenders = []
        for path in REPO_ROOT.rglob("*"):
            if not path.is_file() or ".git" in path.parts:
                continue
            if path.parts[len(REPO_ROOT.parts)].startswith("lib-"):
                continue
            if path.suffix not in (".md", ".py", ".yml", ".yaml"):
                continue
            relative = path.relative_to(REPO_ROOT).as_posix()
            if relative in allowed:
                continue
            content = path.read_text(encoding="utf-8")
            if re.search(r"\bD[012]\b", content):
                offenders.append(relative)
        self.assertEqual(offenders, [])

    def test_legacy_brand_company_and_model_names_do_not_return(self):
        allowed = {
            "PRODUCT_NAMING.md",
            "tests/test_product_naming.py",
        }
        legacy_names = ("Know CO2", "Know CO₂", "KNOWCO2 LLC", "KnowCO2 Model A")
        offenders = []
        for path in REPO_ROOT.rglob("*"):
            if not path.is_file() or ".git" in path.parts:
                continue
            if path.parts[len(REPO_ROOT.parts)].startswith("lib-"):
                continue
            if path.suffix not in (".md", ".py", ".yml", ".yaml"):
                continue
            relative = path.relative_to(REPO_ROOT).as_posix()
            if relative in allowed:
                continue
            content = path.read_text(encoding="utf-8")
            for legacy_name in legacy_names:
                if legacy_name in content:
                    offenders.append("%s: %s" % (relative, legacy_name))
        self.assertEqual(offenders, [])

    def test_brand_company_and_model_metadata_use_production_names(self):
        mqtt = (REPO_ROOT / "knowco2/telemetry/mqtt.py").read_text(encoding="utf-8")
        display = (REPO_ROOT / "knowco2/ui/widgets.py").read_text(encoding="utf-8")
        self.assertIn('"manufacturer": "KnowCO2 LLC"', mqtt)
        self.assertIn('"model": "KC2-01"', mqtt)
        self.assertIn('text="KnowCO2 LLC"', display)


if __name__ == "__main__":
    unittest.main()
