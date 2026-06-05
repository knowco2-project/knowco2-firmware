# knowco2/__init__.py
# KnowCO2 firmware package.
#
# Module map (see ARCHITECTURE.md):
#   version   firmware + CircuitPython version
#   config    immutable constants
#   helpers   dependency-free utilities (log, clamp, etc.)
#   state     shared runtime state (cross-module mutable values)
#   sensors/  CO2 sensor driver abstraction + registry  <-- add sensors here

from . import version

__version__ = version.FIRMWARE_VERSION
