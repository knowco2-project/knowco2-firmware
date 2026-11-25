# knowco2/version.py
# ----------------------------------------------------------------------
# Firmware version string + CircuitPython runtime detection.
# Kept tiny and import-safe so anything (incl. /status JSON) can read it.
# ----------------------------------------------------------------------

import sys as _sys

FIRMWARE_VERSION = "RC-54-USB-v1-test1"


def detect_circuitpython_version():
    """Return the running CircuitPython version, e.g. "10.0.3", or
    "unknown" if it cannot be determined."""
    try:
        impl = getattr(_sys, "implementation", None)
        # The tuple reports 11.0.0 for every 11.0.0 prerelease. The banner
        # includes the actual tag (e.g. 11.0.0-beta.0) on both 10 and 11.
        if getattr(impl, "name", None) == "circuitpython":
            banner = getattr(_sys, "version", "")
            if "CircuitPython " in banner:
                tag = banner.split("CircuitPython ", 1)[1].split()
                if tag:
                    return tag[0].rstrip(";")
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
