# knowco2/sensors/__init__.py
# ----------------------------------------------------------------------
# Sensor registry + auto-detection.
#
# THIS is the single place that knows which sensor drivers exist. The main
# firmware calls detect_sensor(i2c) and gets back one CO2Sensor instance
# (already measuring) or None — it never imports a vendor library directly.
#
# To add a new sensor:
#   1. write knowco2/sensors/your_sensor.py with a CO2Sensor subclass
#   2. add it to SENSOR_DRIVERS below, in priority order
# That's the entire change. See ADDING_A_SENSOR.md.
# ----------------------------------------------------------------------

from .base import CO2Sensor
from .scd4x import SCD4xSensor
from .scd30 import SCD30Sensor
from .sunrise import SunriseSensor
from .stcc4 import STCC4Sensor

# Detection priority: the first driver whose detect() returns an instance
# wins. SCD-4x is tried before SCD-30 to preserve the original behaviour.
SENSOR_DRIVERS = (
    SCD4xSensor,
    SCD30Sensor,
    SunriseSensor,
    STCC4Sensor,
    # ── add new drivers here ──
)


def detect_sensor(i2c):
    """Probe the I2C bus with each registered driver in priority order.

    Returns a started CO2Sensor instance, or None if nothing was found.
    Each driver's detect() is responsible for swallowing its own errors,
    so one missing/incompatible sensor never blocks the others.
    """
    for driver in SENSOR_DRIVERS:
        try:
            sensor = driver.detect(i2c)
        except Exception as e:
            print("sensor detect error (%s):" % getattr(driver, "model", driver), e)
            sensor = None
        if sensor is not None:
            print("Sensor detected:", sensor.model,
                  "serial:", sensor.read_serial(),
                  "low_power:", sensor.supports_low_power)
            return sensor
    print("No supported CO2 sensor found on I2C bus")
    return None


__all__ = ["CO2Sensor", "detect_sensor", "SENSOR_DRIVERS",
           "SCD4xSensor", "SCD30Sensor", "SunriseSensor", "STCC4Sensor"]
