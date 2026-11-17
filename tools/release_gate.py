#!/usr/bin/env python3
"""Report 1.0.0 blockers or fail closed before any publication.

This ledger is NOT a substitute for artifact verification or hardware evidence.
The integration release workflow has no publisher even if every gate is cleared.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = frozenset({
    "ota_power_loss_recovery", "ota_first_boot_health", "customer_state_preservation",
    "production_signing", "runtime_security_profile", "flasher_profile_alignment",
    "real_upgrade_and_capacity", "upstream_security_review", "hardware_regression",
    "wifi_cloud_soak", "repository_release_controls",
})


def unresolved(document: dict) -> list[str]:
    if not isinstance(document, dict) or document.get("schema") != "knowco2.release-readiness.v1":
        raise ValueError("Unknown release readiness schema")
    if document.get("target_version") != "1.0.0":
        raise ValueError("This ledger applies only to 1.0.0")
    if type(document.get("release_allowed")) is not bool:
        raise ValueError("release_allowed must be an explicit boolean")
    gates = document.get("gates")
    if not isinstance(gates, dict) or set(gates) != REQUIRED:
        raise ValueError("Missing or unexpected mandatory release gates")
    pending = []
    for name in sorted(gates):
        gate = gates[name]
        if not isinstance(gate, dict) or gate.get("status") not in ("pending", "blocked", "passed"):
            raise ValueError("Invalid gate status: " + name)
        if not isinstance(gate.get("requirement"), str) or not gate["requirement"].strip():
            raise ValueError("Missing requirement: " + name)
        if gate["status"] != "passed":
            pending.append(name + ": " + gate["requirement"])
        elif not isinstance(gate.get("evidence"), str) or not gate["evidence"].strip():
            raise ValueError("A passing gate needs reviewable evidence: " + name)
    if document["release_allowed"] is not True:
        pending.append("Release authorization is explicitly disabled")
    return pending


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--report", action="store_true")
    modes.add_argument("--enforce", action="store_true")
    args = parser.parse_args()
    try:
        document = json.loads((ROOT / "docs/releases/1.0.0-gates.json").read_text())
        pending = unresolved(document)
    except (OSError, ValueError, TypeError) as error:
        print("RELEASE GATE INVALID: " + str(error))
        return 1
    print("KnowCO2 1.0.0: " + ("BLOCKED" if pending else "ledger gates cleared; artifact review still required"))
    for item in pending:
        print("- " + item)
    return 1 if args.enforce and pending else 0


if __name__ == "__main__":
    raise SystemExit(main())
