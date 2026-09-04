"""Retail USB security profile tests for boot.py."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


def _execute_boot(maintenance):
    calls = []

    board = types.ModuleType("board")
    board.D1 = object()
    board.DISPLAY = types.SimpleNamespace(rotation=0, root_group=None)
    sys.modules["board"] = board

    displayio = types.ModuleType("displayio")
    displayio.Group = lambda: object()
    sys.modules["displayio"] = displayio

    digitalio = types.ModuleType("digitalio")
    digitalio.Pull = types.SimpleNamespace(DOWN="down")

    class _Pin:
        value = maintenance

        def __init__(self, pin):
            self.pin = pin

        def switch_to_input(self, pull=None):
            calls.append(("pin_input", pull))

        def deinit(self):
            calls.append(("pin_deinit",))

    digitalio.DigitalInOut = _Pin
    sys.modules["digitalio"] = digitalio

    storage = types.ModuleType("storage")
    storage.remount = lambda *args, **kwargs: calls.append(("remount", args, kwargs))
    storage.disable_usb_drive = lambda: calls.append(("disable_storage",))
    sys.modules["storage"] = storage

    fake_time = types.ModuleType("time")
    fake_time.sleep = lambda seconds: None
    sys.modules["time"] = fake_time

    for name in ("usb_cdc", "usb_hid", "usb_midi"):
        module = types.ModuleType(name)
        module.disable = lambda n=name: calls.append(("disable", n))
        sys.modules[name] = module

    spec = importlib.util.spec_from_file_location("knowco2_test_boot", REPO_ROOT / "boot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return calls


class BootSecurityTests(unittest.TestCase):
    def tearDown(self):
        for name in ("board", "displayio", "digitalio", "storage", "time", "usb_cdc", "usb_hid", "usb_midi", "knowco2_test_boot"):
            sys.modules.pop(name, None)

    def test_retail_boot_disables_console_and_storage(self):
        calls = _execute_boot(maintenance=False)
        self.assertIn(("disable", "usb_cdc"), calls)
        self.assertIn(("disable_storage",), calls)
        self.assertIn(("disable", "usb_hid"), calls)
        self.assertIn(("disable", "usb_midi"), calls)

    def test_physical_maintenance_boot_keeps_console_and_storage(self):
        calls = _execute_boot(maintenance=True)
        self.assertNotIn(("disable", "usb_cdc"), calls)
        self.assertNotIn(("disable_storage",), calls)
        self.assertIn(("disable", "usb_hid"), calls)
        self.assertIn(("disable", "usb_midi"), calls)


if __name__ == "__main__":
    unittest.main()
