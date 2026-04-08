import board
import digitalio
import storage
import time

# -----------------------------------------------------------------------------
# boot.py
#
# Default behavior:
#   - Hide the USB mass-storage drive so the board does not show as CIRCUITPY
#   - Keep the filesystem available for normal CircuitPython file writes
#
# Override:
#   - Hold D1 at power-up / reset to keep the USB drive visible
# -----------------------------------------------------------------------------

OVERRIDE_PIN = board.D1

override = None

try:
    override = digitalio.DigitalInOut(OVERRIDE_PIN)
    override.switch_to_input(pull=digitalio.Pull.DOWN)

    # Give the pin a moment to settle after power-up.
    time.sleep(0.05)

    if override.value:
        # Override held: host (Mac/PC) gets write access by default. Do not remount.
        pass
    else:
        # Default behavior: make sure the filesystem is writable, then hide USB storage.
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
