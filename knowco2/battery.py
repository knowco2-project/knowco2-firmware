# knowco2/battery.py
# ----------------------------------------------------------------------
# Battery fuel-gauge (MAX17048) over I2C.
# Tries the board I2C bus, then the STEMMA QT bus. Stores the handle in
# state so the rest of the firmware just calls battery.read().
# ----------------------------------------------------------------------

import board

from . import state

try:
    import adafruit_max1704x
except Exception as e:
    adafruit_max1704x = None
    print("max1704x lib IMPORT FAILED:", e)


def _scan(i2c):
    try:
        while not i2c.try_lock():
            pass
        return i2c.scan()
    finally:
        try:
            i2c.unlock()
        except Exception:
            pass


def _init_on_bus(i2c, bus_name):
    state.fuel_gauge = None
    state.fuel_gauge_kind = None
    state.fuel_bus_name = None

    addrs = _scan(i2c)
    print("I2C scan (%s):" % bus_name, [hex(a) for a in addrs])

    if 0x36 in addrs:
        if adafruit_max1704x is None:
            print("0x36 present but adafruit_max1704x import failed")
        else:
            try:
                g = adafruit_max1704x.MAX17048(i2c)
                try:
                    g.reset()
                except Exception:
                    pass
                try:
                    g.quickstart()
                except Exception:
                    pass
                state.fuel_gauge = g
                state.fuel_gauge_kind = "max17048"
                state.fuel_bus_name = bus_name
                print("Battery gauge: MAX17048 @0x36 on", bus_name)
                return True
            except Exception as e:
                print("MAX17048 init failed on %s:" % bus_name, e)
    return False


def init():
    try:
        if _init_on_bus(board.I2C(), "board.I2C"):
            return
    except Exception as e:
        print("Battery gauge init on board.I2C failed:", e)

    if hasattr(board, "STEMMA_I2C"):
        try:
            if _init_on_bus(board.STEMMA_I2C(), "board.STEMMA_I2C"):
                return
        except Exception as e:
            print("Battery gauge init on board.STEMMA_I2C failed:", e)

    print("Battery gauge: not found")


def read():
    """Return (cell_voltage, percent_int) or (None, None)."""
    g = state.fuel_gauge
    if g is None:
        return None, None
    try:
        v = float(g.cell_voltage)
        p = int(round(float(g.cell_percent)))
        p = max(0, min(100, p))
        return v, p
    except Exception as e:
        print("Battery read error:", e)
        return None, None
