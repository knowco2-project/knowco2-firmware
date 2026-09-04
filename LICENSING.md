# Licensing map

KnowCO2 uses separate licensing for original firmware and bundled third-party dependencies.

| Material | Paths | License |
|---|---|---|
| KnowCO2-authored firmware and documentation | `code.py`, `boot.py`, `knowco2/**`, tests, tools, workflows, and repository documentation | MIT |
| Vendored CircuitPython dependencies | `lib-*-release/**` | Respective upstream licenses recorded in `THIRD_PARTY_NOTICES.md` |
| CircuitPython runtime image | `circuitpython-release/10.2.1/**` | MIT plus the additional upstream notices reproduced in `LICENSES/CIRCUITPYTHON-10.2.1.txt` |

The canonical MIT license text for KnowCO2-authored material is in [`LICENSE`](LICENSE).

Copyright notices, license terms, and source locations for bundled libraries are in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md). That file is included with every published firmware archive containing KnowCO2 firmware or vendored dependencies.

The complete distribution notice accompanying the checked-in CircuitPython
10.2.1 image is in
[`LICENSES/CIRCUITPYTHON-10.2.1.txt`](LICENSES/CIRCUITPYTHON-10.2.1.txt).

Release archives place legal material under `assets/legal/` so it remains present on installed devices. Source archives retain the same material at the repository root.
