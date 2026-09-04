import board
import displayio
import digitalio
import storage
import time
import usb_cdc
import usb_hid
import usb_midi

# -----------------------------------------------------------------------------
# boot.py
#
# Default behavior:
#   - Hide the USB mass-storage drive so the board does not show as CIRCUITPY
#   - Disable the USB CDC console/REPL
#   - Keep the filesystem available for normal CircuitPython file writes
#   - Disable USB HID (keyboard/mouse) and MIDI. Neither is used by the
#     firmware, and an enumerated HID keyboard makes iOS hide its on-screen
#     keyboard whenever the device is plugged in for power.
#
# Override:
#   - Hold D1 at power-up / reset to keep the USB drive and console visible
# -----------------------------------------------------------------------------

# Suppress the CircuitPython REPL terminal on the built-in TFT as early as possible,
# and set the correct rotation so no upside-down text is visible during boot.
try:
    board.DISPLAY.rotation = 180
    board.DISPLAY.root_group = displayio.Group()
except Exception:
    pass

# HID and MIDI are unused in both retail and maintenance profiles.
try:
    usb_hid.disable()
except Exception:
    pass
try:
    usb_midi.disable()
except Exception:
    pass

OVERRIDE_PIN = board.D1

override = None

try:
    override = digitalio.DigitalInOut(OVERRIDE_PIN)
    override.switch_to_input(pull=digitalio.Pull.DOWN)

    # Give the pin a moment to settle after power-up.
    time.sleep(0.05)

    if override.value:
        # Physical maintenance profile: the host keeps the default CircuitPython
        # console and writable CIRCUITPY drive. Physical possession is trusted in
        # this explicitly selected mode.
        pass
    else:
        # Retail profile: no REPL/console and no host-mounted CIRCUITPY drive.
        # The firmware still keeps its own filesystem writable for settings.
        try:
            usb_cdc.disable()
        except Exception:
            pass
        try:
            storage.remount("/", readonly=False)
        except Exception:
            pass
        try:
            storage.disable_usb_drive()
        except Exception:
            pass

finally:
    try:
        if override is not None:
            override.deinit()
    except Exception:
        pass
