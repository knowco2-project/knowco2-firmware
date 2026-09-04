import board
import displayio
import digitalio
import storage
import time
import usb_hid
import usb_midi

# -----------------------------------------------------------------------------
# boot.py
#
# Default behavior:
#   - Name the service volume KNOWCO2
#   - Hide the USB mass-storage drive during normal customer operation
#   - Keep the filesystem available for normal CircuitPython file writes
#   - Disable USB HID (keyboard/mouse) and MIDI. Neither is used by the
#     firmware, and an enumerated HID keyboard makes iOS hide its on-screen
#     keyboard whenever the device is plugged in for power.
#
# Override:
#   - Hold button B at power-up / reset to keep the KNOWCO2 service volume visible
# -----------------------------------------------------------------------------

# Suppress the CircuitPython REPL terminal on the built-in TFT as early as possible,
# and set the correct rotation so no upside-down text is visible during boot.
try:
    board.DISPLAY.rotation = 180
    board.DISPLAY.root_group = displayio.Group()
except Exception:
    pass

# Unconditional: applies whether or not the button B override is held. USB CDC
# serial is left enabled so the REPL and print() debugging still work.
try:
    usb_hid.disable()
except Exception:
    pass
try:
    usb_midi.disable()
except Exception:
    pass

SERVICE_VOLUME_LABEL = "KNOWCO2"
MAINTENANCE_BUTTON_PIN = board.D1  # Physical button B.

override = None

try:
    override = digitalio.DigitalInOut(MAINTENANCE_BUTTON_PIN)
    override.switch_to_input(pull=digitalio.Pull.DOWN)

    # Give the pin a moment to settle after power-up.
    time.sleep(0.05)

    maintenance_mode = bool(override.value)

    # CircuitPython creates a fresh filesystem as CIRCUITPY. Once KnowCO2 is
    # installed, keep the product/service volume name stable across updates.
    # The label can only be changed while the microcontroller owns the volume.
    try:
        storage.remount("/", readonly=False)
        filesystem = storage.getmount("/")
        if filesystem.label != SERVICE_VOLUME_LABEL:
            filesystem.label = SERVICE_VOLUME_LABEL
    except Exception:
        pass

    if maintenance_mode:
        # Give the host write access to the visible KNOWCO2 service volume.
        try:
            storage.remount("/", readonly=True)
        except Exception:
            pass
    else:
        # Normal operation: firmware keeps write access and USB storage is hidden.
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
