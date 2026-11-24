#!/usr/bin/env python3
"""Fail if a staged firmware release tree contains host Python bytecode or
device runtime state (settings files hold credentials and must never ship)."""

from __future__ import annotations

import argparse
from pathlib import Path


RUNTIME_STATE_FILES = {
    "settings.json",
    "settings.json.bak",
    "settings.json.tmp",
    "settings.json.new",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()

    root = args.root.resolve()
    offenders: list[str] = []
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if "__pycache__" in rel.parts or path.suffix in {".pyc", ".pyo"}:
            offenders.append(str(rel))

    if offenders:
        raise RuntimeError(
            "host bytecode leaked into release tree: " + ", ".join(offenders)
        )

    state_files = sorted(
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.name in RUNTIME_STATE_FILES
    )
    if state_files:
        raise RuntimeError(
            "device runtime state must not ship in a release: "
            + ", ".join(state_files)
        )

    print(f"Release tree is free of host bytecode and runtime state: {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
