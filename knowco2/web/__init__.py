# knowco2/web/__init__.py
# Web layer: raw-socket HTTP utilities, the configuration portal page,
# the request router / server loop, and the OTA update flow.
#
# Keep this initializer lazy. Importing ``knowco2.web.api_v1_contract`` on a
# workstation must not load routes.py (and therefore CircuitPython-only
# ``storage``). The device entry point still gets the same two callables.


def start_http_server(*args, **kwargs):
    from .routes import start_http_server as _start
    return _start(*args, **kwargs)


def handle_http_client(*args, **kwargs):
    from .routes import handle_http_client as _handle
    return _handle(*args, **kwargs)
