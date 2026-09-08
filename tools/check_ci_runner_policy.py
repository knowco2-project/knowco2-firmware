#!/usr/bin/env python3
"""Fail CI if a firmware GitHub Actions job uses a GitHub-hosted runner.

KnowCO2 firmware CI is intentionally executed on the project's ephemeral AWS
CodeBuild GitHub Actions runner.  This check prevents a future workflow edit
from silently falling back to shared GitHub-hosted runners.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
EXPECTED_PREFIX = "codebuild-knowco2-firmware-${{ github.run_id }}-${{ github.run_attempt }}"
HOSTED_LABEL_RE = re.compile(r"\b(?:ubuntu|macos|windows)-[A-Za-z0-9_.-]+\b", re.IGNORECASE)
RUNS_ON_RE = re.compile(r"^(?P<indent>\s*)runs-on:\s*(?P<value>.*?)\s*$")


def _runs_on_values(text: str) -> list[str]:
    lines = text.splitlines()
    values: list[str] = []
    for index, line in enumerate(lines):
        match = RUNS_ON_RE.match(line)
        if not match:
            continue
        value = match.group("value").strip().strip("'\"")
        if value:
            values.append(value)
            continue

        indent = len(match.group("indent"))
        cursor = index + 1
        while cursor < len(lines):
            candidate = lines[cursor]
            stripped = candidate.strip()
            if not stripped:
                cursor += 1
                continue
            candidate_indent = len(candidate) - len(candidate.lstrip())
            if candidate_indent <= indent:
                break
            if stripped.startswith("-"):
                values.append(stripped[1:].strip().strip("'\""))
            cursor += 1
    return values


def main() -> int:
    failures: list[str] = []
    workflows = sorted(WORKFLOW_DIR.glob("*.y*ml"))
    if not workflows:
        failures.append("no GitHub Actions workflows found")

    for path in workflows:
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT)
        values = _runs_on_values(text)
        if not values:
            failures.append(f"{rel}: no runs-on value found")
            continue
        for value in values:
            if HOSTED_LABEL_RE.search(value):
                failures.append(f"{rel}: GitHub-hosted runner is forbidden: {value}")
            elif value != EXPECTED_PREFIX:
                failures.append(
                    f"{rel}: runner must be {EXPECTED_PREFIX!r}, found {value!r}"
                )

    if failures:
        print("KnowCO2 CI runner policy FAILED:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print(
        "KnowCO2 CI runner policy PASS: every workflow job uses the ephemeral "
        "AWS CodeBuild runner."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
