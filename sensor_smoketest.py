# sensor_smoketest.py
# ----------------------------------------------------------------------
# Minimal on-hardware check of the modular sensor layer ONLY.
#
# Copy `knowco2/` and this file to CIRCUITPY, then rename this to code.py
# (or run it from the REPL). It exercises detection, identity, reading,
# and the low-power switch on your real sensor — without any of the
# display / Wi-Fi / web code, so it can't disturb your working firmware
# build. It is NOT the full product firmware.
# ----------------------------------------------------------------------

import time
import board

from knowco2 import version, config, state
from knowco2 import sensors

print("KnowCO2 sensor smoke test — firmware", version.FIRMWARE_VERSION,
      "| CircuitPython", version.CP_VERSION)

i2c = board.I2C()
state.sensor = sensors.detect_sensor(i2c)

if state.sensor is None:
    print("No sensor detected. Check wiring / STEMMA QT connector.")
    while True:
        time.sleep(1)

s = state.sensor
state.sensor_model_str = s.model
state.scd_serial_str = s.read_serial()
print("Detected:", s.model,
      "| serial:", s.read_serial(),
      "| low_power:", s.supports_low_power,
      "| normal period:", s.normal_period_s, "s")

# Apply any stored calibration the same way the firmware would.
s.set_asc(True)
s.set_altitude(0)
s.set_ambient_pressure(0)

print("Warming up...")
time.sleep(5)

print("Reading every ~%ss (Ctrl-C to stop):" % int(config.SCD_MEASUREMENT_PERIOD))
last = 0.0
samples = 0
while True:
    now = time.monotonic()
    if now - last > 1.0:
        last = now
        try:
            if s.data_ready:
                co2, temp_c, rh = s.read()
                state.last_co2, state.last_temp_c, state.last_rh = co2, temp_c, rh
                samples += 1
                print("CO2 %4d ppm | %.1f C | %.0f%% RH" % (co2, temp_c, rh))
                # After 6 good samples, demonstrate the low-power switch once.
                if samples == 6 and s.supports_low_power:
                    period = s.set_low_power(True)
                    print("--> low-power on; effective period now %ss" % period)
        except RuntimeError as err:
            print("read error (CRC/I2C):", err)
            s.recover(low_power=state.energy_mode)
    time.sleep(0.05)
