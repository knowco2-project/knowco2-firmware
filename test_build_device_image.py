import tempfile
import unittest
from pathlib import Path

from tools.build_device_image import validate_output


class DeviceImagePackagingTests(unittest.TestCase):
    def test_rejects_host_cpython_bytecode(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bad = root / "knowco2" / "__pycache__" / "module.cpython-312.pyc"
            bad.parent.mkdir(parents=True)
            bad.write_bytes(b"bad")
            with self.assertRaises(RuntimeError):
                validate_output(root)

    def test_accepts_clean_image(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "knowco2").mkdir()
            (root / "knowco2" / "module.mpy").write_bytes(b"mpy")
            validate_output(root)


if __name__ == "__main__":
    unittest.main()
