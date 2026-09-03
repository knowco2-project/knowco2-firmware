import board
import displayio
import digitalio
import storage
import time
import usb_cdc
import usb_hid
import usb_midi

try:
    import supervisor
except Exception:
    supervisor = None

# -----------------------------------------------------------------------------
# boot.py — KnowCO2 USB profile
#
# Normal boot:
#   - Hide CIRCUITPY mass storage
#   - Expose the existing CircuitPython console plus a dedicated data CDC port
#   - Disable HID and MIDI
#
# Maintenance boot (hold button B / D1 during reset):
#   - Keep CIRCUITPY visible
#   - Expose the console only; disable the data CDC port
#
# ESP32-S3 effectively has four usable endpoint pairs. Two CDC interfaces use
# all four, so mass storage cannot coexist with both. Maintenance mode therefore
# uses console + CIRCUITPY, while normal mode uses console + data and no drive.
#
# TRANSITION NOTE:
# Keep CONSOLE_IN_NORMAL_MODE=True until knowco2-flasher and the production
# tester have migrated from console log parsing to the versioned USB data API.
# After that migration, set it False so retail devices expose only the safe data
# port during normal operation; holding B will still restore the console.
# -----------------------------------------------------------------------------

CONSOLE_IN_NORMAL_MODE = True
OVERRIDE_PIN = board.D1

# Give desktop operating systems a stable human-readable identity without
# claiming a custom USB VID/PID. A production VID/PID decision can be made
# separately once the protocol is validated on hardware.
if supervisor is not None:
    try:
        supervisor.set_usb_identification(
            manufacturer="KnowCO2 LLC",
            product="KnowCO2 Monitor",
        )
    except Exception:
        pass

# Suppress CircuitPython terminal text on the built-in TFT as early as possible.
try:
    board.DISPLAY.rotation = 180
    board.DISPLAY.root_group = displayio.Group()
except Exception:
    pass

# KnowCO2 does not use USB keyboard/mouse or MIDI interfaces.
try:
    usb_hid.disable()
except Exception:
    pass
try:
    usb_midi.disable()
except Exception:
    pass

override = None
maintenance_mode = False
try:
    override = digitalio.DigitalInOut(OVERRIDE_PIN)
    override.switch_to_input(pull=digitalio.Pull.DOWN)
    time.sleep(0.05)
    maintenance_mode = bool(override.value)
finally:
    try:
        if override is not None:
            override.deinit()
    except Exception:
        pass

# Normal mode keeps the existing console during this development phase and adds
# the dedicated data CDC endpoint used by Linux/macOS/Windows applications.
# Maintenance mode disables data so CIRCUITPY can remain available.
try:
    if maintenance_mode:
        usb_cdc.enable(console=True, data=False)
    else:
        usb_cdc.enable(console=CONSOLE_IN_NORMAL_MODE, data=True)
except Exception:
    pass

if maintenance_mode:
    # Host gets normal CIRCUITPY access. Do not remount or hide the drive.
    pass
else:
    # Firmware owns the filesystem so settings and calibration records can save.
    try:
        storage.remount("/", readonly=False)
    except Exception:
        pass
    try:
        storage.disable_usb_drive()
    except Exception:
        pass
