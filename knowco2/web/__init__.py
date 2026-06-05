# knowco2/web/__init__.py
# Web layer: raw-socket HTTP utilities, the configuration portal page,
# the request router / server loop, and the OTA update flow.
from . import http_util, portal_page, routes  # noqa: F401

# Convenience re-exports so the main loop / wifi can call web.start_http_server().
from .routes import start_http_server, handle_http_client  # noqa: F401
