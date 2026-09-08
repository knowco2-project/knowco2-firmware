#!/usr/bin/env python3
"""Fail if a staged firmware release tree contains host Python bytecode."""

from __future__ import annotations

import argparse
from pathlib import Path


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

    print(f"Release tree is free of host bytecode: {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
