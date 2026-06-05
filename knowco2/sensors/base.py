# knowco2/sensors/base.py
# ----------------------------------------------------------------------
# CO2Sensor — the contract every sensor driver implements.
#
# This is the ONE place that defines what the rest of the firmware is
# allowed to ask a sensor to do.  The main application never touches a
# vendor driver directly and never does `hasattr(scd, "...")` branching.
# Instead it talks to this uniform interface, and each concrete driver
# (scd4x.py, scd30.py, your_sensor.py) hides the vendor-specific details.
#
# To add a new sensor you implement a subclass of CO2Sensor and register
# it in __init__.py.  You do not edit the main firmware.  See
# ADDING_A_SENSOR.md.
# ----------------------------------------------------------------------


class CO2Sensor:
    """Uniform interface for a CO2 / temperature / humidity sensor.

    Subclasses MUST override:
        detect(cls, i2c)          (classmethod)
        data_ready                (property)
        read()
        _start_normal_raw()       (begin/continue normal measurement)

    Subclasses SHOULD override where the hardware supports it:
        read_serial()
        set_asc(), set_altitude(), set_ambient_pressure()
        force_calibration()
        _start_low_power_raw(), supports_low_power
        _stop_raw(), _soft_reset_raw()   (used by recover())
    """

    # Human-readable model name, refined during detect() (e.g. "SCD41").
    model = "CO2"

    # True if the sensor has a genuine low-power measurement mode.
    supports_low_power = False

    # Normal / low-power effective sample periods in seconds. Drivers may
    # override these so the application's staleness watchdog scales correctly.
    normal_period_s = 5.0
    low_power_period_s = 30.0

    def __init__(self, device):
        # `device` is the underlying vendor driver instance.
        self.device = device
        self.serial = None

    # ------------------------------------------------------------------
    # Detection / construction
    # ------------------------------------------------------------------
    @classmethod
    def detect(cls, i2c):
        """Probe the I2C bus. Return a started, ready-to-read instance if
        this sensor is present, else None. Must swallow its own errors and
        return None rather than raising, so the registry can try the next
        driver. Implementations are responsible for any ordering constraints
        (e.g. reading the serial number while idle before starting)."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------
    @property
    def data_ready(self):
        """True when a fresh measurement is available to read()."""
        raise NotImplementedError

    def read(self):
        """Return (co2_ppm: float, temp_c: float, rh: float).
        Only call when data_ready is True. May raise on a CRC/I2C error;
        the caller treats a raised exception as a failed sample."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------
    def read_serial(self):
        """Return a short serial / identity string, or None."""
        return self.serial

    # ------------------------------------------------------------------
    # Calibration & compensation (all best-effort; no-op if unsupported)
    # ------------------------------------------------------------------
    def set_asc(self, enabled):
        """Enable/disable Automatic Self-Calibration."""
        pass

    def set_altitude(self, meters):
        """Set altitude compensation in metres (0 = disabled)."""
        pass

    def set_ambient_pressure(self, hpa):
        """Set ambient-pressure compensation in hPa (0 = disabled)."""
        pass

    def force_calibration(self, ref_ppm):
        """Forced recalibration against a known reference ppm.
        Return True on success, False otherwise."""
        return False

    # ------------------------------------------------------------------
    # Power management
    # ------------------------------------------------------------------
    def set_low_power(self, active):
        """Switch the sensor between low-power and normal measurement.

        Returns the effective sample period in seconds so the caller can
        scale its watchdog / refresh timing. This is generic; drivers
        customise behaviour via _stop_raw / _start_low_power_raw /
        _start_normal_raw rather than overriding this method."""
        import time
        self._safe(self._stop_raw)
        time.sleep(0.3)
        if active and self.supports_low_power:
            if self._safe(self._start_low_power_raw):
                return self.low_power_period_s
        # Fall through to normal measurement (also covers unsupported LP).
        self._safe(self._start_normal_raw)
        return self.normal_period_s

    # ------------------------------------------------------------------
    # Recovery — generic stop -> reset -> restart sequence
    # ------------------------------------------------------------------
    def recover(self, low_power=False):
        """Attempt to recover a wedged sensor. Generic sequence built from
        the driver primitives; drivers rarely need to override this."""
        import time
        self._safe(self._stop_raw)
        time.sleep(0.2)
        self._safe(self._soft_reset_raw)
        time.sleep(0.8)
        if low_power and self.supports_low_power:
            if not self._safe(self._start_low_power_raw):
                self._safe(self._start_normal_raw)
        else:
            self._safe(self._start_normal_raw)
        time.sleep(0.2)

    # ------------------------------------------------------------------
    # Low-level primitives used by set_low_power() / recover().
    # Override these in each driver.
    # ------------------------------------------------------------------
    def _start_normal_raw(self):
        raise NotImplementedError

    def _start_low_power_raw(self):
        """Override only if supports_low_power is True."""
        return False

    def _stop_raw(self):
        pass

    def _soft_reset_raw(self):
        pass

    # ------------------------------------------------------------------
    # Helpers shared by all drivers
    # ------------------------------------------------------------------
    @staticmethod
    def _safe(func, *args, **kwargs):
        """Call func, swallowing exceptions. Returns the result, or None on
        error (so callers can treat None/False as 'did not run')."""
        try:
            return func(*args, **kwargs)
        except Exception:
            return None

    @staticmethod
    def _format_serial(sn):
        """Format a numeric serial (int, or tuple/list of byte/word values)
        into a stable hex string. Returns None if it cannot be formatted."""
        try:
            if callable(sn):
                sn = sn()
            if isinstance(sn, (tuple, list)) and len(sn) > 0 and all(isinstance(x, int) for x in sn):
                if all(0 <= x <= 255 for x in sn):
                    return "".join("%02X" % x for x in sn)
                if all(0 <= x <= 0xFFFF for x in sn):
                    return "-".join("%04X" % (x & 0xFFFF) for x in sn)
            if isinstance(sn, int):
                return "%08X" % (sn & 0xFFFFFFFF)
            if sn is not None:
                return str(sn)
        except Exception:
            pass
        return None
