# SCD4X driver strategy

Decision, 2026-11-10: use **one unmodified Adafruit SCD4X driver**, pinned to the
released **1.4.13** for new complete installs, with the existing small KnowCO2
adapter. No separate SCD43 driver or permanent vendor fork is needed. Physical
qualification remains pending; this is a reviewable candidate, not approval to
install or release it.

## Why the upstream driver is the right foundation

The repository description still mentions SCD40/SCD41, but the actual released
implementation explicitly supports SCD43. [Release 1.4.13][release] points to
commit `2c62569382663b42d79f1780ee647895c58dbc6e`; upstream `main` was identical
when reviewed. It provides public `sensor_variant` / `sensor_variant_name`,
correct FRC failure/signed-correction handling, 30 ms reinit timing and additional
ASC/single-shot controls. These features belong in the upstream driver.

The older 1.4.11 driver already uses the shared periodic measurement commands.
An SCD43 may therefore produce readings with it. That does not make the original
KnowCO2 integration correct: it guesses identity and calls nonexistent low-power
and reset methods. A library update alone cannot repair those application calls.

| Choice | Assessment |
|---|---|
| Keep only 1.4.11 indefinitely | Requires ongoing workarounds and misses upstream fixes |
| Pin upstream 1.4.13, keep a small adapter | **Chosen:** one driver for SCD40/41/43; vendor owns protocol, KnowCO2 owns application lifecycle |
| Add `scd43.py` or fork Adafruit | Duplicates the same protocol and increases future maintenance |
| Upgrade the entire bundle for this fix | Changes unrelated networking/display libraries and broadens hardware qualification unnecessarily |
| Follow upstream `main` automatically | Makes builds change without review; use exact releases, commits and hashes instead |

The KnowCO2 adapter must still select normal/low-power operation, stop before
idle-only settings, resume after FRC, and restore runtime settings after reinit.
These are product responsibilities; Adafruit does not know the application's
desired mode or saved settings. The normal 1.4.13 path uses public APIs. A test
explicitly rejects private vendor access on identity/FRC for that path.

## How this candidate is pinned

Only `lib-2026-05-20-release/adafruit_scd4x.mpy` changes. It is the exact official
CP10 binary from the release asset, not a locally patched build. Its matching
source release is retained for host tests. The original base bundle metadata
remains intact; `component_overrides` records the replacement's release, commit,
archive hashes, source hash and MPY hash. The effective tree and library-set ID
change, and both build manifests include the override provenance.

The dependency verifier catches source/binary drift, wrong-major release assets,
inconsistent metadata and stale snapshot IDs. The candidate approval state
requires hardware qualification. No auto-updating package manager runs on the
device. Other vendor libraries and the CircuitPython runtime are unchanged.

## Existing devices and retirement of the compatibility code

The current release workflow's OTA ZIP omits `lib/`. Consequently, deploying
only application files can leave 1.4.11 installed. This candidate intentionally
keeps the small, tested 1.4.11 identity/FRC fallback for that migration case.
An unknown/unreadable model stays `SCD4x`; it is never inferred from method names.
The generic fallback is not hardware qualification and is not the preferred
production path.

Use an approved complete/dev service update to install the locked library set.
Back up device identity/settings and follow the service procedure; a manufacturing
erase is not required merely to replace this driver. Record the actual installed
driver version and library-set ID. Replacing the driver in the repository or
passing CI does not mean deployed devices have received it.

Before removing the old-driver compatibility path:

1. Qualify 1.4.13 on SCD40/SCD41 and both SCD43 engineering units with the actual
   CircuitPython runtime, board, enclosure and full application.
2. Verify complete/service installation and rollback on an engineering unit.
3. Make minimum library requirements enforceable by the release/updater flow,
   and migrate or explicitly exclude devices still using older libraries.
4. Rebuild/requalify the PR #22 security bootstrap with the new exact library-set
   binding; its existing signed test artifact is not interchangeable.
5. Remove the 1.4.11 fallback and fixture in a separately reviewed migration PR.

Future driver updates follow the same process: review an upstream release,
replace the single locked component if justified, verify official archive
digests, rerun the protocol and packaging tests, and complete physical tests.
The candidate protocol tests load their source from the dependency lock, so
changing that pin automatically tests the newly selected driver.
When a broader bundle refresh includes the chosen driver, remove the override
and update the base snapshot together. Avoid carrying an override indefinitely
after it becomes redundant.

## Evidence and limits

41 protocol tests cover both pinned driver sources; 16 dependency-policy tests
cover pins, manifests, drift rejection and image copying. These are host tests
with simulated time/I2C, not execution of the MPY on a real device. See the
[two-unit validation procedure](scd43-validation.md) and record physical results
before calling SCD43 product support verified. Zero-valued compensation clearing
and calibration-page status/readback remain separate documented limitations.

[release]: https://github.com/adafruit/Adafruit_CircuitPython_SCD4X/releases/tag/1.4.13
