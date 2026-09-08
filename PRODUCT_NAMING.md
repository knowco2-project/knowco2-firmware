# KnowCO2 product naming

Use these names consistently in firmware, the browser portal, documentation,
release notes, support instructions, and integrations.

| Item | Production name | Notes |
|---|---|---|
| Product and brand | **KnowCO2** | One word with this capitalization. Do not write “Know CO2,” “Know CO₂,” or “KNOWCO2” in prose. |
| Legal manufacturer | **KnowCO2 LLC** | Use the registered company capitalization. |
| First product model | **KC2-01** | Use this in discovery metadata and customer-visible specifications. |
| Enclosure controls | **button A**, **button B**, **button C** | Customer-facing instructions must never use board pin names as button names. |
| KnowCO2 service volume | **`KNOWCO2`** | Hidden during normal operation; hold physical button B during power-up/reset to show it. |
| Factory-default CircuitPython volume | **`CIRCUITPY`** | Appears only before KnowCO2's `boot.py` has set the production label. |
| Feather bootloader volume | **`FTHRS3BOOT`** | Board/bootloader identifier; it is not the KnowCO2 service volume. |

The internal electrical mapping is physical button A = `board.D0`, button B =
`board.D1`, and button C = `board.D2`. Raw pin identifiers are appropriate in
hardware initialization, electrical comments, and low-level tests only.

Lowercase `knowco2` remains correct for machine-facing names such as the Python
package, repository, release files, hostnames, MQTT topics, and URLs.

`CO₂` is the preferred human-readable gas name. Use ASCII `CO2` where a
protocol, identifier, filename, font, or compatibility constraint requires it.
