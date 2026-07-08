# knowco2/recovery.py
# ----------------------------------------------------------------------
# Safe-mode auto-recovery.
#
# WHY THIS EXISTS
# ---------------
# CircuitPython enters *safe mode* (code.py never runs; device looks dead)
# after: a hardware watchdog reset (our watchdog uses WatchDogMode.RESET),
# a brownout (weak battery), pressing reset during the ~1 s boot window,
# or a hard fault. For a consumer device that means "screen frozen or
# blank until someone presses reset".
#
# CircuitPython runs /safemode.py when it enters safe mode. Ours simply
# reboots the board — bounded by a retry counter in NVM so a persistent
# fault cannot become an infinite reboot loop. code.py clears the counter
# after the device has been up and healthy for a while.
#
# DELIVERY: the OTA ZIP allow-list historically didn't include
# safemode.py, so this module carries the file's content and (re)writes
# /safemode.py at boot whenever it's missing or outdated. The firmware
# self-heals; no separate install step, and future updates to the
# embedded content propagate automatically.
# ----------------------------------------------------------------------

import storage

from . import state, config
from .helpers import log

try:
    import microcontroller
except Exception:
    microcontroller = None

SAFEMODE_PATH = "/safemode.py"

# NVM layout (see config.py): [MAGIC][consecutive retry count]
_MAGIC = 0xC2

SAFEMODE_CONTENT = '''# KnowCO2 safemode.py v1 — auto-recovery from CircuitPython safe mode.
# Managed by knowco2/recovery.py; edits here are overwritten at boot.
#
# Safe mode means code.py did NOT run (watchdog reset, brownout, reset
# pressed during the boot window, hard fault, ...). For a consumer device
# we reboot automatically, bounded by a retry counter in NVM so a
# persistent fault can't cause an endless reboot loop. The main firmware
# clears the counter after %d s of healthy uptime.

import microcontroller

_MAGIC = %d
_MAX = %d
_IDX_MAGIC = %d
_IDX_COUNT = %d

reason = None
_stay = False
try:
    import supervisor
    reason = supervisor.runtime.safe_mode_reason
    # PROGRAMMATIC = a developer explicitly requested safe mode; honor it.
    _stay = reason == supervisor.SafeModeReason.PROGRAMMATIC
except Exception:
    pass

count = 0
nvm = None
try:
    nvm = microcontroller.nvm
    if nvm is not None:
        if nvm[_IDX_MAGIC] != _MAGIC:
            nvm[_IDX_MAGIC] = _MAGIC
            nvm[_IDX_COUNT] = 0
        count = nvm[_IDX_COUNT]
except Exception:
    nvm = None

print("safemode.py: reason=%%s consecutive=%%d" %% (reason, count))

if (not _stay) and count < _MAX:
    try:
        if nvm is not None:
            nvm[_IDX_COUNT] = count + 1
    except Exception:
        pass
    print("safemode.py: auto-reset (%%d/%%d)" %% (count + 1, _MAX))
    microcontroller.reset()

# Persistent fault (or programmatic safe mode): stay here rather than
# reboot-loop. A single press of the reset button starts the cycle again.
print("safemode.py: staying in safe mode; press reset to retry")
''' % (config.RECOVERY_STABLE_UPTIME_S, _MAGIC, config.RECOVERY_MAX_RETRIES,
       config.RECOVERY_NVM_MAGIC_IDX, config.RECOVERY_NVM_COUNT_IDX)


def _read_counter():
    """Return the consecutive safe-mode recovery count, or 0."""
    if microcontroller is None:
        return 0
    try:
        nvm = microcontroller.nvm
        if nvm is None:
            return 0
        if nvm[config.RECOVERY_NVM_MAGIC_IDX] != _MAGIC:
            return 0
        return int(nvm[config.RECOVERY_NVM_COUNT_IDX])
    except Exception:
        return 0


def clear_counter():
    """Called by code.py after RECOVERY_STABLE_UPTIME_S of healthy uptime."""
    if microcontroller is None:
        return
    try:
        nvm = microcontroller.nvm
        if nvm is None:
            return
        if nvm[config.RECOVERY_NVM_MAGIC_IDX] != _MAGIC:
            nvm[config.RECOVERY_NVM_MAGIC_IDX] = _MAGIC
        if nvm[config.RECOVERY_NVM_COUNT_IDX] != 0:
            nvm[config.RECOVERY_NVM_COUNT_IDX] = 0
            log("recovery", "safe-mode retry counter cleared (stable uptime)")
    except Exception:
        pass


def ensure_safemode_file():
    """(Re)write /safemode.py if missing or outdated. Safe to call every
    boot; writes only when content differs. Skips silently when the
    filesystem is read-only (USB-mounted dev mode)."""
    try:
        existing = None
        try:
            with open(SAFEMODE_PATH, "r") as f:
                existing = f.read()
        except OSError:
            pass
        if existing == SAFEMODE_CONTENT:
            return True
        try:
            storage.remount("/", readonly=False)
        except Exception:
            pass
        try:
            if bool(storage.getmount("/").readonly):
                log("recovery", "cannot install safemode.py (fs read-only)")
                return False
        except Exception:
            pass
        with open(SAFEMODE_PATH, "w") as f:
            f.write(SAFEMODE_CONTENT)
        log("recovery", "safemode.py installed/updated")
        return True
    except Exception as e:
        log("recovery", "safemode.py install failed:", e)
        return False


def init():
    """Boot-time: record how many consecutive safe-mode auto-recoveries
    preceded this boot (0 = clean boot) and ensure /safemode.py exists."""
    state.safe_mode_recoveries = _read_counter()
    if state.safe_mode_recoveries:
        log("recovery", "recovered from safe mode (consecutive: %d)"
            % state.safe_mode_recoveries)
    ensure_safemode_file()
