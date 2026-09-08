# Contributing to KnowCO2 Firmware

Welcome! This firmware runs on real hardware in people's homes, so the
bar is: readable code, tested changes, honest commit messages.

## Ground rules

1. **Desktop-importable, always.** Every module must import on CPython
   3.10+ with the CircuitPython-only modules absent (use guarded
   imports). The test suite depends on this.
2. **Tests before merge.** `python -m py_compile` on everything, plus
   the suites in `tests/` (CI runs them on every PR — see
   tests/README.md to run locally in Docker).
3. **No blocking work in button handlers.** Flip a flag; do the work
   from the main loop. See MAINTAINABILITY.md for the pattern.
4. **Native modules for hot paths.** Per-pixel/per-byte Python loops
   are a code smell; check bitmaptools/struct/binascii first.
5. **Comments explain why.** The interesting part of embedded code is
   the failure story, not the syntax.

## Making a change

- Branch from the default branch; name it `feature/...` or `fix/...`
- Keep PRs to one concern; mechanical refactors separate from behavior
- If you fix a bug that reached hardware, add the failing scenario to
  the matching test suite in the same PR
- Update the version string only in release PRs
  (`RC-<n>-<Theme>-v<rev>` — the theme names the headline change)

## Testing on hardware

Service-volume workflow: hold physical button B while pressing Reset to expose
the `KNOWCO2` volume with host write access; copy your tree; eject properly.
Serial REPL: `screen /dev/tty.usbmodem* 115200`, Ctrl-C to stop the
firmware, Ctrl-D to reload. OTA: web UI -> Update, unlocked by admin
password or holding A+B for 3 s.

Use the product, company, model, button, and USB names in
[PRODUCT_NAMING.md](PRODUCT_NAMING.md) for every customer-visible string.

## Releases (maintainers)

1. Merge to default branch, bump `knowco2/version.py`
2. Build the OTA zip: `zip -r knowco2-update-<version>.zip . -x "tests/*" ".github/*" "*.pyc" "*.git*"`
3. Record the SHA-256 in the release notes
4. GitHub release tagged with the version
