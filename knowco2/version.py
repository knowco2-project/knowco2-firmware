# knowco2/version.py
# ----------------------------------------------------------------------
# Firmware version string + CircuitPython runtime detection.
# Kept tiny and import-safe so anything (incl. /status JSON) can read it.
# ----------------------------------------------------------------------

import sys as _sys

FIRMWARE_VERSION = "RC-43-Energy-v5"


def detect_circuitpython_version():
    """Return the running CircuitPython version, e.g. "10.0.3", or
    "unknown" if it cannot be determined."""
    try:
        impl = getattr(_sys, "implementation", None)
        if impl is not None and hasattr(impl, "version"):
            v = impl.version
            if isinstance(v, tuple) and len(v) >= 3:
                return "%d.%d.%d" % (v[0], v[1], v[2])
            return str(v)
        sv = getattr(_sys, "version", "") or ""
        idx = sv.find("CircuitPython ")
        if idx >= 0:
            tail = sv[idx + len("CircuitPython "):]
            return tail.split()[0].rstrip(";")
        return sv.split(";")[0].strip() or "unknown"
    except Exception:
        return "unknown"


CP_VERSION = detect_circuitpython_version()
