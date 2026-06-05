# knowco2/ui/__init__.py
# Presentation layer: display widgets + screen logic.
#
# Importing this builds the displayio widget tree (widgets) and loads the
# screen-logic functions (screens). code.py registers the relevant functions
# as runtime hooks so the lower layers can drive the UI without importing it.
from . import widgets  # noqa: F401  (creates the display objects at import)
from . import screens  # noqa: F401

# Re-export the screen functions at package level for convenient wiring.
from .widgets import show_status  # noqa: F401
from .screens import (  # noqa: F401
    color_for_co2, graph_color_index_for_co2, apply_color_scheme,
    apply_alert_colors, compute_trend_arrow, refresh_text,
    build_wifi_qr_payload, build_url_qr_payload, make_or_update_qrs,
    refresh_apinfo_screen, update_wifi_indicator, update_visibility,
    redraw_graph,
)
