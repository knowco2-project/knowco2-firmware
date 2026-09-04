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
# Normal retail boot:
#   - Name the service volume KNOWCO2
#   - Hide the USB mass-storage drive during normal customer operation
#   - Disable the USB CDC console/REPL
#   - Expose only the bounded, read-mostly USB data API
#   - Keep the filesystem available for normal CircuitPython file writes
#   - Disable USB HID (keyboard/mouse) and MIDI. Neither is used by the
#     firmware, and an enumerated HID keyboard makes iOS hide its on-screen
#     keyboard whenever the device is plugged in for power.
#
# Physical maintenance boot:
#   - Hold button B at power-up / reset to keep the KNOWCO2 service volume visible
#     with the CircuitPython console available and data API disabled
# -----------------------------------------------------------------------------

CONSOLE_IN_NORMAL_MODE = False
SERVICE_VOLUME_LABEL = "KNOWCO2"
MAINTENANCE_BUTTON_PIN = board.D1  # Physical button B.

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

# HID and MIDI are unused in both retail and maintenance profiles.
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
    override = digitalio.DigitalInOut(MAINTENANCE_BUTTON_PIN)
    override.switch_to_input(pull=digitalio.Pull.DOWN)
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

finally:
    try:
        if override is not None:
            override.deinit()
    except Exception:
        pass

# Normal mode exposes only the bounded data endpoint used by host applications;
# maintenance mode restores the full-trust console and disables the data API.
try:
    if maintenance_mode:
        usb_cdc.enable(console=True, data=False)
    else:
        usb_cdc.enable(console=CONSOLE_IN_NORMAL_MODE, data=True)
except Exception:
    # If a data-only configuration is unsupported, fail closed in retail mode.
    if not maintenance_mode:
        try:
            usb_cdc.disable()
        except Exception:
            pass

if maintenance_mode:
    # Give the host write access to the visible KNOWCO2 service volume.
    try:
        storage.remount("/", readonly=True)
    except Exception:
        pass
else:
    # Firmware owns the filesystem so settings and calibration records can save;
    # the service volume stays hidden from the host.
    try:
        storage.disable_usb_drive()
    except Exception:
        pass
