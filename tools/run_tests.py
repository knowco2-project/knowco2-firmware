#!/usr/bin/env python3
"""Run every hardware-free suite without cross-suite hardware-mock leakage.

OTA filesystem tests use temporary directories. No root permissions, connected
hardware, network request, or dependency-monitor run is required by this runner.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_paths(root: Path = ROOT) -> list[Path]:
    return sorted((root / "tests").glob("test_*.py")) + [
        root / "test_build_device_image.py", root / "test_sensors.py",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="Print discovered suites without executing")
    args = parser.parse_args()
    suites = test_paths()
    if args.list:
        for path in suites:
            print(path.relative_to(ROOT))
        return 0
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment.pop("PYTHONOPTIMIZE", None)  # Legacy suites use bare asserts.
    failures = []
    for path in suites:
        relative = path.relative_to(ROOT)
        print("\n=== " + str(relative) + " ===", flush=True)
        try:
            result = subprocess.run([sys.executable, "-u", str(path)],
                                    cwd=path.parent, env=environment, timeout=180)
            if result.returncode:
                failures.append(str(relative) + " (exit " + str(result.returncode) + ")")
        except (OSError, subprocess.TimeoutExpired) as error:
            failures.append(str(relative) + " (" + type(error).__name__ + ")")
    print("\nSuites: %d passed, %d failed, %d total" %
          (len(suites) - len(failures), len(failures), len(suites)), flush=True)
    for failure in failures:
        print("FAIL: " + failure, flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
