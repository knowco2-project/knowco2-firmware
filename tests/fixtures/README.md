# SCD4x driver fixture

`adafruit_scd4x_1_4_11.py` is an unmodified, MIT-licensed source fixture from
[Adafruit commit cb3c7f1186c5ceba466de9d11404808d3db06691](https://github.com/adafruit/Adafruit_CircuitPython_SCD4X/blob/cb3c7f1186c5ceba466de9d11404808d3db06691/adafruit_scd4x.py)
(tag `1.4.11`). Its license is in `LICENSE.adafruit_scd4x`.

The locked bundle commit `258a344fa879a2aace636956fed52e1148b4176b` points
`libraries/drivers/scd4x` to this exact source commit. The baseline `.mpy`
contained version `1.4.11`. The upstream source retains its build-time version
placeholder, unchanged.

- Source SHA-256: `4f7578a9cd4003c2e37f69e1887f574a6129e5d08883f3cc250e18d13ca5c827`
- Previous 1.4.11 `lib-2026-05-20-release/adafruit_scd4x.mpy` SHA-256:
  `6972734d4a2a96bc89e28a8dde12656e3ecac34ba5bb6da6543b265e52dfcf69`

`adafruit_scd4x_1_4_13.py` is the unmodified source from the official
[1.4.13 source release archive](https://github.com/adafruit/Adafruit_CircuitPython_SCD4X/releases/download/1.4.13/adafruit-circuitpython-scd4x-py-1.4.13.zip).
It matches upstream commit `2c62569382663b42d79f1780ee647895c58dbc6e` with only
Adafruit's release-time `__version__` substitution. Both archives were checked
against GitHub's published release SHA-256 digests before extraction.

| 1.4.13 file | SHA-256 |
|---|---|
| Source archive | `bd70d18259b59e1bd3954a92bf2bbbf8ab73474b89987e0a239ce15b76145704` |
| CP10 MPY archive | `ef9b48fa8b381387572f394ab43f5a63a2d990a227463cd92e0427c765c00644` |
| Source fixture | `711c28250f95a1ae569c9c30a89f33ea31c6c3878c3b106ce48b6e346dad4c7f` |
| Installed MPY | `5a546ae7ac4c78b9d9a31eddce3e8a5ee016999a6a445e9de1fdbd8ac222315f` |

The candidate installs that official CP10 MPY as the single SCD4X library.
The dependency lock verifies both the new fixture and installed binary. The
1.4.11 fixture remains to test application-only OTA compatibility with older
installed libraries. Run the same protocol scenarios against both releases.

The host tests load these sources with simulated CircuitPython dependencies and
I2C responses. They do not execute the compiled `.mpy` or prove hardware behavior.
Source fixtures are test-only, outside the device image's packaged paths. Both
retain upstream copyright headers and use `LICENSE.adafruit_scd4x` (MIT).
