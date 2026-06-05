# Adding a new sensor

KnowCO2's sensor support is a small **driver registry**. To add a sensor you
write **one new file** and add **one line** to the registry. You never touch
the main firmware, the display code, the web portal, or the main loop.

That is the whole point of the abstraction: a contributor can add a Senseair
Sunrise, an SCD41 variant, a future NDIR module, etc., without being able to
break anything else.

---

## The contract

Every driver subclasses `CO2Sensor` (`knowco2/sensors/base.py`). The base class
already implements `set_low_power()` and `recover()` generically; you only
supply the vendor-specific pieces.

**Required:**

| Member | What it does |
|---|---|
| `detect(cls, i2c)` *(classmethod)* | Probe the bus. Return a started instance if your sensor is present, else `None`. Must not raise. |
| `data_ready` *(property)* | `True` when a fresh reading is available. |
| `read()` | Return `(co2_ppm, temp_c, rh)`. Only called when `data_ready`. May raise on a bad sample. |
| `_start_normal_raw()` | Begin/continue normal periodic measurement. |

**Override when the hardware supports it:**

| Member | Default |
|---|---|
| `read_serial()` | returns `self.serial` |
| `set_asc(enabled)` | no-op |
| `set_altitude(meters)` | no-op |
| `set_ambient_pressure(hpa)` | no-op |
| `force_calibration(ppm)` | returns `False` |
| `supports_low_power` + `_start_low_power_raw()` | LP unsupported |
| `_stop_raw()` / `_soft_reset_raw()` | no-op (used by `recover()`) |
| `model`, `normal_period_s`, `low_power_period_s` | `"CO2"`, `5.0`, `30.0` |

The application only ever calls the **public** methods. It never does
`hasattr(sensor, "...")` — all of that branching is hidden inside your driver.

---

## Step 1 — write the driver

Create `knowco2/sensors/my_sensor.py`:

```python
import time

try:
    import my_vendor_lib
except Exception:
    my_vendor_lib = None

from .base import CO2Sensor


class MySensor(CO2Sensor):
    model = "MYSENSOR"
    supports_low_power = False     # set True only if real LP mode exists
    normal_period_s = 5.0

    @classmethod
    def detect(cls, i2c):
        if my_vendor_lib is None:
            return None
        try:
            dev = my_vendor_lib.Device(i2c)
        except Exception:
            return None            # not on the bus — let the registry try the next
        try:
            inst = cls(dev)
            inst.serial = inst._format_serial(getattr(dev, "serial_number", None))
            inst._start_normal_raw()   # leave the sensor measuring
            return inst
        except Exception as e:
            print("MySensor init failed:", e)
            return None

    @property
    def data_ready(self):
        return self.device.data_ready          # or .data_available, etc.

    def read(self):
        d = self.device
        return (d.CO2, d.temperature, d.relative_humidity)

    # --- optional but recommended ---
    def set_asc(self, enabled):
        self._safe(setattr, self.device, "self_calibration_enabled", bool(enabled))

    def set_altitude(self, meters):
        if meters:
            self._safe(setattr, self.device, "altitude", int(meters))

    def set_ambient_pressure(self, hpa):
        if hpa:
            self._safe(self.device.set_ambient_pressure, int(hpa))

    def force_calibration(self, ref_ppm):
        try:
            self.device.force_calibration(int(ref_ppm))
            return True
        except Exception:
            return False

    # --- primitives used by recover() / set_low_power() ---
    def _start_normal_raw(self):
        self.device.start_periodic_measurement()
        return True

    def _stop_raw(self):
        self.device.stop_periodic_measurement()

    def _soft_reset_raw(self):
        self.device.soft_reset()
```

`_safe(...)` and `_format_serial(...)` are inherited from `CO2Sensor`, so
best-effort calls stay one-liners.

## Step 2 — register it

In `knowco2/sensors/__init__.py`:

```python
from .my_sensor import MySensor

SENSOR_DRIVERS = (
    SCD4xSensor,
    SCD30Sensor,
    MySensor,        # <-- one line. order = detection priority
)
```

Done. `detect_sensor(i2c)` now tries your sensor too.

---

## Notes & gotchas

- **Ordering inside `detect()` matters.** The SCD-4x must read its serial number
  *while idle*, before `start_periodic_measurement()`. If your sensor has a
  similar constraint, do it inside `detect()` — that's the one place that knows.
- **`detect()` must never raise.** Catch everything and return `None` so the
  registry can fall through to the next driver. Constructing the vendor object
  on an empty bus is the normal way a "not present" sensor reveals itself.
- **`read()` may raise.** A CRC/I2C error should propagate; the main loop counts
  consecutive failures and calls `recover()` after `SCD_MAX_FAILS_BEFORE_RESET`.
- **Low power:** only set `supports_low_power = True` if there's a genuine
  reduced-rate mode. `set_low_power(True)` falls back to normal measurement for
  sensors without one, and reports the correct effective period either way.
- **Units:** `read()` returns CO2 in ppm, temperature in °C, RH in %. The app
  handles °C/°F display conversion itself.

## Testing without hardware

You can exercise a driver on a desktop with a fake device object — see
`test_sensors.py` at the project root for the pattern (it stubs the Adafruit
libraries in `sys.modules`, then asserts detection priority, the read tuple,
the low-power period, and the recover() call sequence).

---

## Worked example: Senseair Sunrise (a CO2-only, no-library driver)

`knowco2/sensors/sunrise.py` is a complete real driver added with exactly the
two steps above. It's instructive because it differs from the SCD parts:

- **No vendor library.** It talks raw I2C (`i2c.writeto_then_readfrom`) to the
  Sunrise at address `0x68`, proving a driver doesn't need an Adafruit library —
  it only has to satisfy the `CO2Sensor` contract.
- **No humidity channel.** Its `read()` returns `(co2, temp_c, None)`. The UI
  was made to tolerate `rh is None` (shows `--%`), so nothing else had to change.
- **Detection by bus scan.** `detect()` checks for `0x68` in `i2c.scan()` and
  does one sanity read before claiming the bus.

The only change outside that file was adding `SunriseSensor` to
`SENSOR_DRIVERS` in `knowco2/sensors/__init__.py`. `code.py`, the UI, the web
routes, telemetry, and settings were untouched — which is the whole point of
the abstraction.

> The CO2 (`0x06`) and temperature (`0x08`) reads and the `0x68` address are
> straight from the Senseair I2C spec (TDE5531). The calibration / soft-reset
> register writes follow the same doc but are best-effort and wrapped so a
> wrong value can't crash the firmware — confirm the command bytes against the
> datasheet revision for your unit before relying on field calibration.


## Second worked example: Sensirion STCC4 (library present, hardware later)

`knowco2/sensors/stcc4.py` wraps Adafruit's `adafruit_stcc4` library. It shows
two more patterns:

- **Optional dependency, drop-in ready.** `import adafruit_stcc4` is wrapped in
  try/except. If the library isn't on the board, `detect()` returns `None` and
  the firmware runs normally — so the driver can live in the package now and
  start working the moment you `circup install adafruit_stcc4` and plug a sensor
  in. Nothing else changes.
- **Library quirks hidden behind the contract.** Reading `.CO2` is what
  refreshes temperature/humidity, so `read()` reads CO2 first. There's no
  data-ready line, so sampling is paced to the measurement period. Low power
  maps to single-shot mode (30 s); `set_ambient_pressure()` -> the library's
  `pressure_compensation()`; `force_calibration()` -> `forced_recalibration()`
  (0xFFFF = failure). The rest of the firmware sees none of this.

Added with the same two steps: one new file + one line in `SENSOR_DRIVERS`.
