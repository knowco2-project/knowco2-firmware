#!/usr/bin/env python3
"""Replay input-conformance scenarios and emit versioned, non-release evidence."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from model import InputDevice, MODEL_VERSION, replay

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_value(root, *args):
    try:
        return subprocess.check_output(["git", "-C", str(root), *args],
                                       stderr=subprocess.DEVNULL, text=True, timeout=5).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def execute(root, case):
    result = {"id": case["id"], "catalog_id": case["catalog_id"], "title": case["title"],
              "scope": "input_conformance_only", "product_acceptance": "not_implemented",
              "expected_actions": case["expected_actions"]}
    try:
        d = replay(root, case)
        result.update(actual_actions=d.action_names(), trace=d.trace, deliveries=d.delivered,
                      elapsed_simulated_ms=d.now_ms, power_epochs=d.epoch,
                      source_sha256=d.source_sha256, config_sha256=d.config_sha256,
                      hold_config=d.hold_config)
        result["status"] = "passed" if result["actual_actions"] == case["expected_actions"] else "failed"
        if result["status"] == "failed":
            result["reason"] = "candidate gesture output differs from independent fixture expectation"
    except Exception as exc:
        result.update(status="error", reason=f"{type(exc).__name__}: {exc}")
    return result


def build_report(root, fixture_path, *, workers=1, case_id=None):
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    if set(data) != {"schema_version", "cases"} or data["schema_version"] != 1:
        raise ValueError("unsupported scenario schema")
    cases = data["cases"]
    if not isinstance(cases, list) or not 1 <= len(cases) <= 128:
        raise ValueError("expected 1..128 cases")
    ids = [c["id"] for c in cases]
    if len(set(ids)) != len(ids) or any(not isinstance(i, str) for i in ids):
        raise ValueError("case IDs must be unique strings")
    if case_id:
        cases = [c for c in cases if c["id"] == case_id]
        if not cases:
            raise ValueError("unknown case ID")
    if type(workers) is not int or not 1 <= workers <= 8:
        raise ValueError("workers must be in 1..8")
    for case in cases:
        if not isinstance(case["expected_actions"], list) or any(
                not isinstance(a, str) for a in case["expected_actions"]):
            raise ValueError("expected_actions must be a list of strings")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda case: execute(root, case), cases))
    model = InputDevice(root)
    dirty = git_value(root, "status", "--porcelain=v1", "--untracked-files=all", "--",
                      "knowco2/buttons.py", "knowco2/config.py", "tests/virtual_device")
    return {
        "schema_version": 1,
        "scope": "input_conformance_only",
        "release_qualified": False,
        "model_version": MODEL_VERSION,
        "runtime": f"CPython {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "workers": workers,
        "candidate": {
            "git_commit": git_value(root, "rev-parse", "HEAD"),
            "worktree_dirty": None if dirty is None else bool(dirty),
            "executed_source": {"knowco2/buttons.py": model.source_sha256},
            "consumed_config": {"knowco2/config.py": model.config_sha256},
        },
        "model_sha256": digest(HERE / "model.py"),
        "runner_sha256": digest(HERE / "run.py"),
        "fixture_sha256": digest(fixture_path),
        "passed": sum(r["status"] == "passed" for r in results),
        "failed": sum(r["status"] != "passed" for r in results),
        "results": results,
        "not_executed": ["boot.py", "code.py", "firmware display renderer", "sensor drivers",
                         "network transports", "storage", "CircuitPython/MPY on physical board"],
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--case", dest="case_id")
    args = p.parse_args()
    report = build_report(ROOT, HERE / "scenarios.json", workers=args.workers, case_id=args.case_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for result in report["results"]:
        print(f'{result["status"].upper():6} {result["id"]} {result["title"]}')
    print(f'Input conformance: {report["passed"]} passed, {report["failed"]} failed. '
          'NOT full-device or physical release acceptance.')
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
