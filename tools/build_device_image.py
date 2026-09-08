#!/usr/bin/env python3
"""Build the on-device KnowCO2 filesystem image deterministically.

This script copies the firmware source tree into an output directory, compiles
application modules to CircuitPython .mpy bytecode with the explicitly pinned
mpy-cross binary, and copies the locked CircuitPython library snapshot.

Entry-point files (code.py and boot.py) remain as source because CircuitPython
expects them by those names. Non-Python assets (for example translation .pack
files and bitmaps) are copied unchanged. Host CPython bytecode and __pycache__
directories are always excluded.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

EXCLUDED_DIRS = {"__pycache__", ".git"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
ENTRYPOINTS = {"code.py", "boot.py"}


def should_skip(path: Path) -> bool:
    return any(part in EXCLUDED_DIRS for part in path.parts) or path.suffix in EXCLUDED_SUFFIXES


def copy_non_python_tree(src_root: Path, dst_root: Path) -> None:
    for src in sorted(src_root.rglob("*")):
        rel = src.relative_to(src_root)
        if should_skip(rel):
            continue
        if src.is_dir():
            (dst_root / rel).mkdir(parents=True, exist_ok=True)
            continue
        if src.suffix == ".py":
            continue
        dst = dst_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def compile_module(mpy_cross: Path, src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [str(mpy_cross), "-o", str(dst), str(src)],
        check=True,
    )


def compile_python_tree(src_root: Path, dst_root: Path, mpy_cross: Path) -> None:
    for src in sorted(src_root.rglob("*.py")):
        rel = src.relative_to(src_root)
        if should_skip(rel):
            continue
        dst = dst_root / rel.with_suffix(".mpy")
        compile_module(mpy_cross, src, dst)


def copy_entrypoints(source_root: Path, output_root: Path) -> None:
    for name in sorted(ENTRYPOINTS):
        src = source_root / name
        if not src.is_file():
            raise FileNotFoundError(f"required entrypoint missing: {src}")
        shutil.copy2(src, output_root / name)


def copy_locked_libraries(library_root: Path, output_root: Path) -> None:
    dst_root = output_root / "lib"
    for src in sorted(library_root.rglob("*")):
        rel = src.relative_to(library_root)
        if should_skip(rel):
            continue
        dst = dst_root / rel
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def validate_output(output_root: Path) -> None:
    offenders = []
    for p in output_root.rglob("*"):
        rel = p.relative_to(output_root)
        if "__pycache__" in rel.parts or p.suffix in EXCLUDED_SUFFIXES:
            offenders.append(str(rel))
    if offenders:
        raise RuntimeError("host bytecode leaked into device image: " + ", ".join(offenders))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--library-root", required=True, type=Path)
    parser.add_argument("--mpy-cross", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()

    source_root = args.source_root.resolve()
    library_root = args.library_root.resolve()
    mpy_cross = args.mpy_cross.resolve()
    output_root = args.output_root.resolve()

    if not mpy_cross.is_file():
        raise FileNotFoundError(f"mpy-cross not found: {mpy_cross}")
    if not library_root.is_dir():
        raise FileNotFoundError(f"library root not found: {library_root}")

    output_root.mkdir(parents=True, exist_ok=True)

    copy_entrypoints(source_root, output_root)

    knowco2_src = source_root / "knowco2"
    knowco2_dst = output_root / "knowco2"
    if not knowco2_src.is_dir():
        raise FileNotFoundError(f"firmware package missing: {knowco2_src}")
    copy_non_python_tree(knowco2_src, knowco2_dst)
    compile_python_tree(knowco2_src, knowco2_dst, mpy_cross)

    assets_src = source_root / "assets"
    if assets_src.is_dir():
        copy_non_python_tree(assets_src, output_root / "assets")

    copy_locked_libraries(library_root, output_root)
    validate_output(output_root)

    print(f"Built device image at {output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
