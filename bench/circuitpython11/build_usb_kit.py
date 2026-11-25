"""Make a USB-only bench ZIP from a clean checkout; never access an attached board."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mpy-cross", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    lock = json.loads((ROOT / "dependencies/firmware-build-lock.json").read_text())
    compiler = args.mpy_cross.resolve()
    compiler_id = subprocess.check_output([str(compiler), "--version"], text=True).strip()
    if not compiler_id.startswith("CircuitPython " + lock["mpy_cross"]["version"] + " on "):
        parser.error("use the CircuitPython mpy-cross version pinned in the production lock")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT).strip():
        parser.error("build from a clean checkout; put the ZIP outside the source tree")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    subprocess.run([sys.executable, "tools/firmware_dependencies.py", "verify"], cwd=ROOT, check=True)
    with tempfile.TemporaryDirectory() as tmp:
        device = Path(tmp) / "device"
        subprocess.run([sys.executable, "tools/build_device_image.py", "--source-root", ".",
                        "--library-root", lock["library_bundle"]["vendored_path"],
                        "--mpy-cross", str(compiler), "--output-root", str(device)], cwd=ROOT, check=True)
        with zipfile.ZipFile(args.output, "x", compression=zipfile.ZIP_DEFLATED) as z:
            for path in sorted(device.rglob("*")):
                if path.is_file():
                    z.write(path, "device/" + path.relative_to(device).as_posix())
            z.write(HERE / "display_bench.py", "device/display_bench.py")
            z.write(HERE / "README.md", "START-HERE.md")
            for notice in ("LICENSE", "LICENSING.md", "THIRD_PARTY_NOTICES.md"):
                z.write(ROOT / notice, "notices/" + notice)
            z.writestr("BUILD.txt", "EXPERIMENTAL / USB ONLY\nBoard: " + lock["board"]["id"]
                       + "\nTest runtime: 11.0.0-beta.0\nCommit: " + revision
                       + "\nCompiler: " + compiler_id + "\nLibrary tree: "
                       + lock["library_bundle"]["vendored_git_tree_sha"] + "\n")
    print(args.output)


if __name__ == "__main__":
    main()
