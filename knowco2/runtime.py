# knowco2/runtime.py
# ----------------------------------------------------------------------
# Cross-layer hooks (a tiny service registry).
#
# WHY THIS EXISTS
# ---------------
# The original firmware has an unavoidable call cycle: the networking code
# reports progress to the UI (show_status), asks the web layer to (re)start
# the HTTP server, and asks the UI to redraw QR codes — while the UI and web
# layers in turn drive networking. If every module imported every other
# module directly, you'd get import-time cycles.
#
# Instead, the lower layers (net, telemetry, settings) call these hooks. The
# UI / web / boot code *registers* the real implementations once at startup
# via register(...). Until then the defaults are safe no-ops, so a module can
# be imported and unit-tested on its own.
#
# This keeps the dependency graph acyclic: net/telemetry depend only on
# `runtime`, never on `ui` or `web` directly.
# ----------------------------------------------------------------------


def _noop(*args, **kwargs):
    return None


def _default_show_status(msg):
    # Safe fallback before the UI registers the real one.
    print("[status]", msg)


# --- UI hooks ---
show_status = _default_show_status
update_wifi_indicator = _noop
make_or_update_qrs = _noop
refresh_apinfo_screen = _noop
apply_color_scheme = _noop
update_visibility = _noop
refresh_text = _noop


def _default_compute_trend_arrow():
    return "-"


compute_trend_arrow = _default_compute_trend_arrow

# Called from inside long blocking operations (e.g. graph redraw) so a button
# press is never silently dropped. Registered by code.py.
poll_buttons = _noop

# --- web hook ---
def _default_start_http_server():
    return False


start_http_server = _default_start_http_server


def register(**hooks):
    """Install real implementations. Call once at boot, e.g.:

        from knowco2 import runtime, ui, web
        runtime.register(
            show_status=ui.show_status,
            update_wifi_indicator=ui.update_wifi_indicator,
            make_or_update_qrs=ui.make_or_update_qrs,
            refresh_apinfo_screen=ui.refresh_apinfo_screen,
            apply_color_scheme=ui.apply_color_scheme,
            start_http_server=web.start_http_server,
        )

    Unknown keys are ignored so callers can register a partial set.
    """
    g = globals()
    for name, fn in hooks.items():
        if fn is not None and name in g:
            g[name] = fn
