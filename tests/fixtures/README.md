# SCD4x driver fixture

`adafruit_scd4x_1_4_11.py` is an unmodified, MIT-licensed source fixture from
[Adafruit commit cb3c7f1186c5ceba466de9d11404808d3db06691](https://github.com/adafruit/Adafruit_CircuitPython_SCD4X/blob/cb3c7f1186c5ceba466de9d11404808d3db06691/adafruit_scd4x.py)
(tag `1.4.11`). Its license is in `LICENSE.adafruit_scd4x`.

The locked bundle commit `258a344fa879a2aace636956fed52e1148b4176b` points
`libraries/drivers/scd4x` to this exact source commit. The checked-in `.mpy`
contains version `1.4.11`. The upstream source retains its build-time version
placeholder, unchanged.

- Source SHA-256: `4f7578a9cd4003c2e37f69e1887f574a6129e5d08883f3cc250e18d13ca5c827`
- Existing `lib-2026-05-20-release/adafruit_scd4x.mpy` SHA-256:
  `6972734d4a2a96bc89e28a8dde12656e3ecac34ba5bb6da6543b265e52dfcf69`

The host tests load this source with simulated CircuitPython dependencies and
I2C responses. They do not execute the compiled `.mpy` or prove hardware behavior.
The fixture is test-only; it does not change the firmware library snapshot or
dependency lock, and it is outside the device image's packaged paths.
