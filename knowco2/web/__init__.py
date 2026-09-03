# knowco2/web/__init__.py
# Web layer: raw-socket HTTP utilities, the configuration portal page,
# the request router / server loop, and the OTA update flow.
from . import http_util, portal_page, routes  # noqa: F401

# The main loop already calls web.handle_http_client() on every pass. Keep USB
# servicing behind this same cooperative boundary so code.py stays untouched and
# the Linux transport cannot diverge from the browser-onboarding main-loop path.
try:
    from .. import usb_api
except Exception:
    usb_api = None


# Convenience re-export used by wifi/runtime hooks.
start_http_server = routes.start_http_server


def handle_http_client():
    routes.handle_http_client()
    if usb_api is not None:
        try:
            usb_api.poll()
        except Exception:
            # USB is optional; it must never take down sensing, display, Wi-Fi,
            # browser onboarding, cloud upload, or OTA recovery.
            pass
