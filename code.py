# code.py — KnowCO2 firmware entry point
# Target: Adafruit Feather ESP32-S3 Reverse TFT (CircuitPython 10.x)
#
# This is the thin application layer. All subsystems live in the `knowco2`
# package; this file builds the UI, wires the cross-layer hooks, runs the boot
# sequence, and drives the main loop. Adding a new sensor needs NO change here
# (see knowco2/sensors/ and ADDING_A_SENSOR.md).
# ----------------------------------------------------------------------

import time
import gc
import board
import displayio
import digitalio

try:
    import microcontroller
except Exception:
    microcontroller = None

try:
    import wifi
except ImportError:
    wifi = None

# --- package: core + subsystems ---
from knowco2 import state, config, version, runtime, helpers
from knowco2 import settings as settings_mod
from knowco2 import battery, ids, sensors
from knowco2.net import wifi as wifi_mod, ntp as ntp_mod
from knowco2.telemetry import cloud as cloud_mod, mqtt as mqtt_mod
from knowco2 import web
from knowco2.helpers import log


# ======================================================================
#  STARTUP SPLASH (logo-only)  — shown before the main UI is built
# ======================================================================
def _show_logo_splash(disp):
    f = None
    try:
        group = displayio.Group()
        bg_bitmap = displayio.Bitmap(1, 1, 1)
        bg_palette = displayio.Palette(1)
        bg_palette[0] = config.SPLASH_BG
        group.append(displayio.TileGrid(bg_bitmap, pixel_shader=bg_palette))
        f = open(config.SPLASH_BMP, "rb")
        odb = displayio.OnDiskBitmap(f)
        logo = displayio.TileGrid(odb, pixel_shader=odb.pixel_shader)
        logo.x = (disp.width - odb.width) // 2
        logo.y = (disp.height - odb.height) // 2
        group.append(logo)
        disp.root_group = group
        disp.refresh()
        time.sleep(config.SPLASH_SECONDS)
    except Exception as e:
        print("Splash failed:", e)
    finally:
        try:
            # Empty Group (not None) — None can hard-fault the display driver.
            disp.root_group = displayio.Group()
        except Exception:
            pass
        if f:
            f.close()
        gc.collect()


try:
    board.DISPLAY.rotation = 180
except Exception:
    pass
_show_logo_splash(board.DISPLAY)

# Build the UI (this creates the displayio widget tree) AFTER the splash.
from knowco2 import ui
from knowco2.ui import widgets as W

# ======================================================================
#  WIRE CROSS-LAYER HOOKS
#  Lower layers (net / telemetry / settings / web) call these; here we
#  install the real UI / web / button implementations.
# ======================================================================
runtime.register(
    show_status=ui.show_status,
    update_wifi_indicator=ui.update_wifi_indicator,
    make_or_update_qrs=ui.make_or_update_qrs,
    refresh_apinfo_screen=ui.refresh_apinfo_screen,
    apply_color_scheme=ui.apply_color_scheme,
    update_visibility=ui.update_visibility,
    refresh_text=ui.refresh_text,
    compute_trend_arrow=ui.compute_trend_arrow,
    start_http_server=web.start_http_server,
    poll_buttons=lambda: _poll_buttons(),
)


# ======================================================================
#  BUTTONS / INPUT
# ======================================================================
btn_a = digitalio.DigitalInOut(board.D0)
btn_a.switch_to_input(pull=digitalio.Pull.UP)
btn_b = digitalio.DigitalInOut(board.D1)
btn_b.switch_to_input(pull=digitalio.Pull.DOWN)
btn_c = digitalio.DigitalInOut(board.D2)
btn_c.switch_to_input(pull=digitalio.Pull.DOWN)


def read_a():
    return not btn_a.value


def read_b():
    return btn_b.value


def read_c():
    return btn_c.value


def _poll_buttons():
    """Poll button B during blocking operations so a press is never dropped.
    Registered as runtime.poll_buttons and called from inside redraw_graph."""
    try:
        b = read_b()
        if b and not state.prev_b:
            state._btn_b_pending = True
        state.prev_b = b
    except Exception:
        pass


def mode_name():
    return ["Text Only", "Big CO2", "Graph Only"][state.display_mode]


# ======================================================================
#  SENSOR HELPERS  (use the CO2Sensor driver abstraction)
# ======================================================================
def apply_energy_mode(active):
    """Enter/exit Low Power mode. The sensor-specific part goes through the
    driver's set_low_power(), which returns the effective sample period."""
    state.energy_mode = active
    state.settings["energy_mode"] = active
    state._save_deferred_ts = time.monotonic() + 2.0

    if state.sensor is not None:
        try:
            state._scd_period_effective = state.sensor.set_low_power(active)
        except Exception as e:
            print("Energy mode sensor switch failed:", e)
            state._scd_period_effective = 5.0

    try:
        board.DISPLAY.brightness = config.ENERGY_LP_BRIGHTNESS if active else 1.0
    except Exception:
        pass
    try:
        W.lp_badge_label.hidden = not active
    except Exception:
        pass
    try:
        state.last_scd_sample_ts = time.monotonic()
    except Exception:
        pass

    ui.show_status("LP Mode: ON - hold A to exit" if active else "LP Mode: OFF")
    print("Energy mode:", "ON" if active else "OFF",
          "| SCD period:", state._scd_period_effective, "s")


def _sensor_recover():
    """Recover a wedged sensor via the driver, with cooldown + escalation to
    an MCU reset after too many consecutive recoveries."""
    if state.sensor is None:
        return
    now = time.monotonic()
    if state._wd is not None:
        try:
            state._wd.feed()
        except Exception:
            pass
    if (now - state.last_scd_reset) < config.SCD_RESET_COOLDOWN_SEC:
        return
    state.last_scd_reset = now
    state.scd_crc_failures = 0
    state.scd_recoveries += 1
    try:
        state.sensor.recover(low_power=state.energy_mode)
        ui.show_status("SCD: recovered")
    except Exception as e:
        ui.show_status("SCD: reset fail")
        log("scd_reset", "SCD recover failed:", e, min_interval=2.0)
    if state.scd_recoveries >= config.SCD_MAX_RECOVERIES_BEFORE_RESET:
        log("scd_reset", "SCD recoveries exceeded; MCU reset", state.scd_recoveries, min_interval=2.0)
        ui.show_status("SCD: restarting")
        time.sleep(0.5)
        try:
            if microcontroller is not None:
                microcontroller.reset()
        except Exception:
            pass
        state.scd_recoveries = 0


def check_battery_boot():
    """Warn (without blocking Wi-Fi) if the battery is critically low at boot."""
    if state.fuel_gauge is None:
        return
    try:
        v, p = battery.read()
        if v is None:
            return
        print("Battery at boot: %.2f V  %s%%" % (v, str(p) if p is not None else "?"))
        if v < config.BATT_BOOT_WARN_V:
            try:
                W.status_label.text = "Battery low - keep USB connected"
                W.th_label.text = "%.2fV  Charging..." % v
                W.th_label.hidden = False
                W.display.root_group = W.main_group
                time.sleep(4)
                W.status_label.text = ""
                W.th_label.text = ""
            except Exception:
                pass
    except Exception as e:
        print("Battery boot check error:", e)


# ======================================================================
#  BOOT SEQUENCE
# ======================================================================
ids.init_ids()
ids.init_pair_code()
ids.init_mdns_hostname()

# DHCP hostname so the device shows as "knowco2-xxxx" on the network.
if wifi is not None and state.mdns_hostname:
    try:
        wifi.radio.hostname = state.mdns_hostname
    except Exception:
        pass

# --- Sensor detection (driver registry picks SCD4x/SCD30/...) ---
state.sensor = None
state.scd_init_failed = False
try:
    _i2c = board.I2C()
    state.sensor = sensors.detect_sensor(_i2c)
    if state.sensor is None:
        raise RuntimeError("No supported CO2 sensor found on I2C bus")
    state.sensor_model_str = state.sensor.model
    state.scd_serial_str = state.sensor.read_serial()
    W.status_label.text = "Warming up..."
    time.sleep(5)
    W.status_label.text = ""
    state.last_scd_sample_ts = time.monotonic()
except Exception as e:
    state.sensor = None
    state.scd_init_failed = True
    state.last_scd_sample_ts = time.monotonic()
    print("SCD init failed:", e)
    try:
        ui.show_status("Sensor init failed")
    except Exception:
        pass

# --- Settings (load + apply) ---
settings_mod.load_settings()
settings_mod.ensure_ap_credentials()
settings_mod.apply_settings()

# Apply stored calibration to the sensor now that settings are loaded.
if state.sensor is not None:
    try:
        state.sensor.set_asc(bool(state.settings.get("asc_enabled", True)))
    except Exception:
        pass
    try:
        _alt = state.settings.get("altitude", 0)
        if _alt:
            state.sensor.set_altitude(int(_alt))
    except Exception:
        pass
    try:
        _ap = state.settings.get("ambient_pressure", 0)
        if _ap:
            state.sensor.set_ambient_pressure(int(_ap))
    except Exception:
        pass

# --- Battery + initial screen ---
battery.init()
check_battery_boot()
ui.update_visibility()

# --- Wi-Fi: prefer STA if configured, else AP (with background retry) ---
if (state.settings.get("sta_ssid") or "").strip() and (state.settings.get("sta_password") or "").strip():
    if not wifi_mod.switch_to_sta():
        state._sta_fallback = True
        wifi_mod.switch_to_ap()
else:
    wifi_mod.switch_to_ap()

# --- Restore Low Power mode if it was active before reboot ---
if state.settings.get("energy_mode", False):
    apply_energy_mode(True)

# ======================================================================
#  MAIN LOOP  (orchestration)
# ======================================================================
last_sensor = 0.0
last_apinfo_refresh = 0.0
last_batt_refresh = 0.0
last_wifi_ind_refresh = 0.0
state.cached_vbat = None
state.cached_pct = None
last_dim_check = 0.0
# Deferred settings save: set to (now + delay) when a button triggers a settings
# change that doesn't need an instant flash write.  Avoids blocking the main loop
# on the button press itself — the actual save happens once the deadline passes.
state._save_deferred_ts = 0.0


# ======================================================================
#  HARD WATCHDOG (consumer-safety)
#  If the main loop stalls (e.g., I2C hang), reset the MCU.
#  Enabled right before the main loop to avoid resets during boot sleeps.
# ======================================================================
state._wd = None
try:
    from watchdog import WatchDogMode
    if microcontroller is not None:
        state._wd = microcontroller.watchdog
        # 20 s timeout: WiFi connect() can block up to ~15 s; watchdog is also
        # fed explicitly before other long-running operations.
        state._wd.timeout = 20
        state._wd.mode = WatchDogMode.RESET
except Exception as e:
    state._wd = None
    print("watchdog unavailable:", e)


while True:
    # Feed hardware watchdog each loop so any hard stall triggers a reset.
    if state._wd is not None:
        try:
            state._wd.feed()
        except Exception:
            pass
    now = time.monotonic()


    if state.fs_readonly and not state.fs_warned:
        state.fs_warned = True
        ui.show_status("USB mode: settings won't save")

    if state.status_timeout > 0 and now > state.status_timeout:
        W.status_label.text = ""
        state.status_timeout = 0.0

    a_now = read_a()
    b_now = read_b()
    c_now = read_c()

    # D0 (A) — short press toggles °C/°F  |  hold 2 s → toggle LP mode.
    # This mirrors button C (D2) which also uses short/long-press patterns.
    if a_now and (not state.prev_a):
        state._btn_a_hold_start = now
        state._btn_a_hold_fired = False

    if a_now and (state._btn_a_hold_start is not None) and (not state._btn_a_hold_fired):
        if (now - state._btn_a_hold_start) >= config.LP_A_HOLD_SECONDS:
            state._btn_a_hold_fired = True
            apply_energy_mode(not state.energy_mode)
            state.settings["energy_mode"] = state.energy_mode

    if (not a_now) and state.prev_a:
        # Button A released — process as short press if hold did not fire
        if (state._btn_a_hold_start is not None) and (not state._btn_a_hold_fired):
            if state.screen == config.SCREEN_REGULATORY:
                # Any short press on regulatory screen returns to info screen.
                state.screen = config.SCREEN_APINFO
                ui.update_visibility()
                ui.refresh_apinfo_screen()
            elif state.screen == config.SCREEN_MAIN:
                state.temp_mode = "C" if state.temp_mode == "F" else "F"
                state.settings["temp_mode"] = state.temp_mode
                state._save_deferred_ts = now + 1.5
                ui.refresh_text()
                ui.show_status("Temp: " + state.temp_mode)
        state._btn_a_hold_start = None
        state._btn_a_hold_fired = False

    # D1 (B) — track hold start on rising edge (for regulatory screen on SCREEN_APINFO)
    if b_now and (not state.prev_b):
        state._btn_b_hold_start = now
        state._btn_b_hold_fired = False

    # D1 (B) — fire hold action: open regulatory screen when held on SCREEN_APINFO
    if b_now and (state._btn_b_hold_start is not None) and (not state._btn_b_hold_fired):
        if (now - state._btn_b_hold_start) >= config.B_HOLD_SECONDS:
            if state.screen == config.SCREEN_APINFO:
                state._btn_b_hold_fired = True
                state.screen = config.SCREEN_REGULATORY
                ui.update_visibility()

    # D1 (B) — on release: handle short press or return from regulatory screen.
    # Also handles presses captured during blocking ops via _btn_b_pending.
    if (not b_now) and (state.prev_b or state._btn_b_pending):
        state._btn_b_pending = False
        if state.screen == config.SCREEN_REGULATORY:
            # Any release on the regulatory screen returns to info screen.
            state.screen = config.SCREEN_APINFO
            ui.update_visibility()
            ui.refresh_apinfo_screen()
        elif not state._btn_b_hold_fired:
            # Existing short-press behaviour: APINFO → MAIN, or cycle display mode.
            if state.screen == config.SCREEN_APINFO:
                state.screen = config.SCREEN_MAIN
                ui.update_visibility()
            if state.screen == config.SCREEN_MAIN:
                state.display_mode = (state.display_mode + 1) % 3
                state.settings["display_mode"] = state.display_mode
                state._save_deferred_ts = now + 1.5  # persist to flash shortly after, not during press
                ui.update_visibility()
                # If the user switched into graph mode, schedule a redraw rather than doing it immediately.
                if state.display_mode == 2:
                    state.graph_refresh_needed = True
                ui.refresh_text()
                ui.show_status("Mode: " + mode_name())
        state._btn_b_hold_start = None
        state._btn_b_hold_fired = False

    # D2 (C) short press toggles screen, hold toggles Wi-Fi mode
    if c_now and (not state.prev_c):
        state.d2_hold_start = now
        state.d2_hold_fired = False

    if c_now and state.d2_hold_start is not None and (not state.d2_hold_fired):
        if (now - state.d2_hold_start) >= config.D2_HOLD_SECONDS:
            state.d2_hold_fired = True
            # Toggle Wi-Fi mode
            if state.wifi_mode == config.WIFI_MODE_STA:
                ui.show_status("Switching to AP...")
                state._sta_fallback = False  # user explicitly chose AP; don't auto-switch back
                wifi_mod.switch_to_ap(force_restart=True)
            else:
                ui.show_status("Switching to STA...")
                if not wifi_mod.switch_to_sta():
                    ui.show_status("STA failed; AP")
                    wifi_mod.switch_to_ap(force_restart=True)

    if (not c_now) and state.prev_c:
        # released
        if state.d2_hold_start is not None and (not state.d2_hold_fired):
            # short press behaviour:
            #   - When on SCREEN_REGULATORY: return to info screen.
            #   - When on SCREEN_APINFO in AP mode: cycle QR page (0->1->0).
            #   - Otherwise: toggle between SCREEN_MAIN and SCREEN_APINFO.
            if state.screen == config.SCREEN_REGULATORY:
                state.screen = config.SCREEN_APINFO
                ui.update_visibility()
                ui.refresh_apinfo_screen()
            elif state.screen == config.SCREEN_APINFO and state.wifi_mode == config.WIFI_MODE_AP:
                state._qr_page = 1 - state._qr_page          # toggle 0<->1
                state._last_wifi_payload = None         # force QR rebuild for new page
                ui.make_or_update_qrs(state.settings.get("ap_ssid", ""), state.settings.get("ap_password", ""), state.ip_str_cached or "192.168.4.1")
            else:
                state.screen = config.SCREEN_APINFO if state.screen == config.SCREEN_MAIN else config.SCREEN_MAIN
                ui.update_visibility()
                if state.screen == config.SCREEN_APINFO:
                    state._qr_page = 0           # always start at page 0 (WiFi QR) when entering
                    state._last_wifi_payload = None
                    if state.wifi_mode == config.WIFI_MODE_AP:
                        ui.make_or_update_qrs(state.settings.get("ap_ssid", ""), state.settings.get("ap_password", ""), state.ip_str_cached or "192.168.4.1")
                    ui.refresh_apinfo_screen()
                else:
                    ui.refresh_text()
                    if state.display_mode == 2:
                        state.graph_refresh_needed = True
        state.d2_hold_start = None
        state.d2_hold_fired = False

    state.prev_a = a_now
    state.prev_b = b_now
    state.prev_c = c_now

    _wifi_ind_interval = 10.0 if state.energy_mode else 1.0
    if now - last_wifi_ind_refresh > _wifi_ind_interval:
        last_wifi_ind_refresh = now
        ui.update_wifi_indicator()

    _batt_interval = 30.0 if state.energy_mode else 2.0
    if now - last_batt_refresh > _batt_interval:
        last_batt_refresh = now
        vv, pp = battery.read()
        if vv is not None:
            state.cached_vbat, state.cached_pct = vv, pp
        # Update low-battery warning banner
        try:
            _batt_low = (state.fuel_gauge is not None and
                         state.cached_pct is not None and
                         state.cached_pct < config.BATT_WARN_PCT)
            W.batt_warn_label.hidden = not (state.screen == config.SCREEN_MAIN and _batt_low)
            if _batt_low:
                W.batt_warn_label.text = "!! BATT %d%%" % int(state.cached_pct)
        except Exception:
            pass

    if state.screen == config.SCREEN_APINFO and (now - last_apinfo_refresh > (10.0 if state.energy_mode else 1.0)):
        last_apinfo_refresh = now
        ui.refresh_apinfo_screen()

    # Poll the sensor less often and handle CRC failures gracefully
    if now - last_sensor > 1.0:
        last_sensor = now
        if state.sensor is None:
            if state.scd_init_failed and (not state.sensor_warned):
                ui.show_status("Sensor unavailable")
                state.sensor_warned = True
        else:
            try:
                # Driver abstraction: data_ready + read() -> (co2, temp_c, rh).
                if state.sensor.data_ready:
                    co2, temp_c, rh = state.sensor.read()

                    # reset failure counters on a successful read
                    state.scd_crc_failures = 0
                    state.scd_recoveries = 0

                    # Instantaneous rate of change (ppm/s) vs the previous sample.
                    prev_co2 = state.last_co2
                    if prev_co2 is not None:
                        state.rate_of_change = (co2 - prev_co2) / state._scd_period_effective
                    else:
                        state.rate_of_change = None

                    state.last_co2_prev = state.last_co2
                    state.last_co2 = co2
                    state.last_temp_c = temp_c
                    state.last_rh = rh
                    state.last_scd_sample_ts = now

                    if state.screen == config.SCREEN_MAIN:
                        ui.refresh_text()
                        if state.last_co2 is not None:
                            ui.apply_alert_colors(state.last_co2)
                            if state.alerts_enabled:
                                if state.last_co2 >= state.ALERT_THRESHOLD:
                                    if not state.alert_triggered:
                                        ui.show_status("ALERT: %d ppm" % int(state.last_co2))
                                        state.alert_triggered = True
                                else:
                                    state.alert_triggered = False

                    state.co2_history.append(state.last_co2)
                    if len(state.co2_history) > state.MAX_POINTS:
                        state.co2_history[:] = state.co2_history[-state.MAX_POINTS:]

                    if state.screen == config.SCREEN_MAIN and state.display_mode == 2:
                        state.graph_refresh_needed = True

            except RuntimeError as err:
                # CRC / data-integrity error: count and recover after a few in a row.
                state.scd_crc_failures += 1
                log("scd_crc", "SCD read error:", err, "fails:", state.scd_crc_failures, min_interval=1.0)
                if state.scd_crc_failures >= config.SCD_MAX_FAILS_BEFORE_RESET:
                    _sensor_recover()
            except Exception as err:
                log("scd_other", "SCD unexpected error:", err, min_interval=1.0)

            # Staleness watchdog: if no fresh sample within the timeout, recover.
            try:
                _effective_scd_timeout = max(config.SCD_SAMPLE_TIMEOUT,
                                             state._scd_period_effective * 2.5)
                _scd_age = time.monotonic() - state.last_scd_sample_ts
                if _scd_age > _effective_scd_timeout:
                    ui.show_status("SCD: timeout")
                    _sensor_recover()
                    state.last_scd_sample_ts = time.monotonic()
            except Exception:
                pass

    # Update sensor-frozen banner each loop so it appears/clears immediately.
    try:
        _scd_age_now = time.monotonic() - state.last_scd_sample_ts
        # In LP mode the sensor updates every 30 s; use 1.5× the effective
        # period to avoid a false "SENSOR ERR" banner between samples.
        _effective_frozen_warn = max(config.SENSOR_FROZEN_WARN_SEC,
                                     state._scd_period_effective * 1.5)
        _frozen = _scd_age_now > _effective_frozen_warn
        if _frozen != state.sensor_frozen_shown:
            state.sensor_frozen_shown = _frozen
            W.sensor_frozen_label.hidden = not (state.screen == config.SCREEN_MAIN and state.sensor_frozen_shown)
    except Exception:
        pass

    # Last-resort hard MCU reset if the sensor has been frozen beyond SENSOR_HARD_RESET_SEC.
    try:
        _effective_hard_reset = max(config.SENSOR_HARD_RESET_SEC,
                                    state._scd_period_effective * 4.0)
        if (time.monotonic() - state.last_scd_sample_ts) > _effective_hard_reset:
            ui.show_status("SCD: hard reset")
            time.sleep(0.5)
            if microcontroller is not None:
                microcontroller.reset()
    except Exception:
        pass

    # NTP sync (STA only) — rate-limited so failed attempts don't stall the main loop.
    if state.wifi_mode == config.WIFI_MODE_STA:
        try:
            overdue = (not state.ntp_synced) or state.ntp_sync_pending or ((now - state.last_ntp_sync) > config.NTP_SYNC_INTERVAL)
            attempt_ok = (now - state.last_ntp_attempt) >= config.NTP_MIN_RETRY_S
            due = overdue and attempt_ok
        except Exception:
            due = False
        if due:
            state.last_ntp_attempt = now
            ntp_mod.ntp_sync(force=False)

        # Cloud upload (periodic) - STA only
        if state.cloud_enabled and state.wifi_mode == config.WIFI_MODE_STA:
            interval = cloud_mod.cloud_next_interval()
            # In LP mode (or critical battery) reduce upload rate further
            if state.energy_mode:
                interval = max(interval, state.cloud_interval_sec * config.ENERGY_LP_CLOUD_MULT)
            if state.cached_pct is not None and state.cached_pct < config.BATT_CRIT_PCT:
                interval = interval * 2  # critical battery: halve upload rate
            if now - state.last_cloud_send > interval:
                state.last_cloud_send = now
                payload = {
                    "device_id": state.settings.get("device_id", "co2-node-1"),
                    "ts": int(time.time()),
                    "co2": state.last_co2,
                    "temp_c": state.last_temp_c,
                    "rh": state.last_rh,
                    "battery_pct": state.cached_pct,
                    "battery_v": state.cached_vbat,
                    "hwid": state.hwid_hex,
                    "scd_serial": state.scd_serial_str,
                    "board_id": state.board_id_str,
                }
                ok = cloud_mod.cloud_send(payload)
                if ok:
                    state.cloud_failures = 0
                    state.cloud_last_ok = time.monotonic()
                    # Do not display "Cloud: OK" as a status message.
                else:
                    state.cloud_failures += 1

        # MQTT publish (periodic) - STA only
        mqtt_enabled = state.settings.get("mqtt_enabled", False)
        if mqtt_enabled and state.settings.get("mqtt_broker", "").strip():
            mqtt_interval = max(15, int(state.settings.get("mqtt_interval_sec", 60) or 60))
            if state.energy_mode:
                mqtt_interval = mqtt_interval * config.ENERGY_LP_MQTT_MULT
            if now - state.last_mqtt_send > mqtt_interval:
                state.last_mqtt_send = now
                mqtt_mod.publish_to_mqtt()

        # Adafruit IO publish (periodic) - STA only
        aio_enabled = state.settings.get("aio_enabled", False)
        if aio_enabled and state.settings.get("aio_username", "").strip() and state.settings.get("aio_key", "").strip():
            aio_interval = max(15, int(state.settings.get("aio_interval_sec", 60) or 60))
            if state.energy_mode:
                aio_interval = aio_interval * config.ENERGY_LP_AIO_MULT
            if now - state.last_aio_send > aio_interval:
                state.last_aio_send = now
                mqtt_mod.publish_to_adafruit_io()

    # If we're in AP mode but the HTTP socket died, restart it.
    try:
        if state.wifi_mode == config.WIFI_MODE_AP and state.http_server_sock is None:
            web.start_http_server()
    except Exception:
        pass

    # Background STA auto-reconnect: if startup STA failed, retry every 90 s so
    # the device connects once the router becomes reachable (e.g. after reboot).
    # Stops after _STA_AUTO_RETRY_MAX attempts to avoid looping forever.
    # Cleared when the user manually holds D2 to stay in AP mode.
    if (state.wifi_mode == config.WIFI_MODE_AP and state._sta_fallback
            and state._sta_auto_retry_count < config._STA_AUTO_RETRY_MAX
            and (state.settings.get("sta_ssid") or "").strip()):
        if (now - state.last_sta_auto_retry) >= config._STA_AUTO_RETRY_INTERVAL:
            state.last_sta_auto_retry = now
            state._sta_auto_retry_count += 1
            ui.show_status("WiFi: connecting...")
            if wifi_mod.switch_to_sta():
                state._sta_fallback = False
                state._sta_auto_retry_count = 0
            else:
                wifi_mod.switch_to_ap()

    # If a graph redraw has been scheduled, perform it once the main loop is otherwise idle.
    if state.graph_refresh_needed and state.screen == config.SCREEN_MAIN and state.display_mode == 2 and (not state.graph_drawing):
        try:
            ui.redraw_graph()
        except Exception as e:
            log('graph', 'Graph redraw error:', e, min_interval=2.0)
        # Clear the request flag; new samples or mode changes will set it again.
        state.graph_refresh_needed = False

    # Display dimming schedule (checks every 60 s, requires NTP)
    if (now - last_dim_check) >= 60.0:
        last_dim_check = now
        if state.settings.get("dim_enabled", False) and state.ntp_synced:
            try:
                import rtc as _rtc_dim
                hour = _rtc_dim.RTC().datetime.tm_hour
                start_h = int(state.settings.get("dim_start_hour", 22) or 22)
                end_h = int(state.settings.get("dim_end_hour", 7) or 7)
                dim_pct = max(0, min(100, int(state.settings.get("dim_brightness", 10) or 10)))
                # Handle overnight ranges (e.g. 22–7)
                if start_h > end_h:
                    in_dim = (hour >= start_h) or (hour < end_h)
                else:
                    in_dim = start_h <= hour < end_h
                target_brightness = (dim_pct / 100.0) if in_dim else 1.0
                # LP mode takes the lower of dim schedule and LP brightness.
                if state.energy_mode:
                    target_brightness = min(target_brightness, config.ENERGY_LP_BRIGHTNESS)
                try:
                    board.DISPLAY.brightness = target_brightness
                except Exception:
                    pass
            except Exception:
                pass
        elif not state.settings.get("dim_enabled", False):
            # Restore full brightness only when dimming is off AND LP mode
            # is not active.  LP mode manages its own brightness via
            # apply_energy_mode() and must not be overridden here.
            if not state.energy_mode:
                try:
                    board.DISPLAY.brightness = 1.0
                except Exception:
                    pass

    # Memory maintenance + monitor (very low overhead)
    if (now - state.last_gc_ts) >= config.MEM_MONITOR_INTERVAL_S:
        try:
            gc.collect()
            free_mem = gc.mem_free()
            alloc = gc.mem_alloc()
        except Exception:
            free_mem = 0
            alloc = 0
        state.last_gc_ts = now
        state.mem_samples += 1
        if free_mem:
            if free_mem < state.mem_free_min:
                state.mem_free_min = free_mem
            if free_mem > state.mem_free_max:
                state.mem_free_max = free_mem
            if state.mem_samples == 1:
                state.mem_free_ema = float(free_mem)
            else:
                state.mem_free_ema = (0.2 * float(free_mem)) + (0.8 * state.mem_free_ema)

    # Flush deferred settings save once the deadline has passed.
    if state._save_deferred_ts > 0.0 and now >= state._save_deferred_ts:
        state._save_deferred_ts = 0.0
        settings_mod.save_settings()

    web.handle_http_client()
    time.sleep(config.ENERGY_LP_SLEEP_S if state.energy_mode else 0.01)
