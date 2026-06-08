# knowco2/web/portal_page.py
# ----------------------------------------------------------------------
# The configuration portal HTML page (Wi-Fi, alerts, calibration, cloud,
# MQTT, device ID, OTA, 9-language UI, accessibility).
#
# This is the single largest block of the original firmware. Its body is
# the EXACT original markup, moved here unchanged. The only addition is the
# re-binding header below, which pulls the handful of values the page reads
# out of the shared modules and binds them to the original local names, so
# not one byte of the template had to be edited.
# ----------------------------------------------------------------------

import gc
import json

from .. import state, config, version
from . import i18n as _i18n


def render_settings_page():
    # --- re-bind the names the original function read from module scope ---
    settings = state.settings
    FIRMWARE_VERSION = version.FIRMWARE_VERSION
    MAX_WEB_POINTS = config.MAX_WEB_POINTS
    LOW_THRESHOLD_DEFAULT = config.LOW_THRESHOLD_DEFAULT
    MED_THRESHOLD_DEFAULT = config.MED_THRESHOLD_DEFAULT
    ALERT_THRESHOLD_DEFAULT = config.ALERT_THRESHOLD_DEFAULT
    MAX_POINTS_DEFAULT = config.MAX_POINTS_DEFAULT
    WIFI_MODE_STA = config.WIFI_MODE_STA
    co2_history = state.co2_history
    display_mode = state.display_mode
    ip_str_cached = state.ip_str_cached
    mdns_hostname = state.mdns_hostname
    temp_mode = state.temp_mode
    wifi_mode = state.wifi_mode
    # --- original body (verbatim) ---
    # Collect garbage before building the ~35 KB settings HTML string.
    # Without this the string concatenation can fragment the heap enough
    # to trigger a MemoryError or, on some CP builds, a hard fault.
    gc.collect()
    # keep your existing page structure, but show mdns hint if STA
    data_points = co2_history[-MAX_WEB_POINTS:]
    ints = []
    for v in data_points:
        if v is None:
            continue
        if isinstance(v, (int, float)):
            try:
                ints.append(int(v))
            except (TypeError, ValueError):
                pass
    initial_json = json.dumps(ints)

    checked_alerts = "checked" if settings.get("alerts_enabled", True) else ""
    scale = settings.get("graph_scale_mode", "fixed")
    max_points = int(settings.get("max_points", MAX_POINTS_DEFAULT))
    ap_ssid = settings.get("ap_ssid", "knowco2")
    sta_ssid = settings.get("sta_ssid", "")
    device_id = settings.get("device_id", "co2-node-1")
    # Embed colorblind_mode so the canvas chart uses the same palette as the device.
    web_cb_mode = "true" if settings.get("colorblind_mode", False) else "false"

    cloud_enabled_checked = "checked" if settings.get("cloud_enabled", False) else ""
    cloud_api = settings.get("cloud_api_url", "")
    # If no cloud API URL is stored yet, prefill with the default knowco2 API endpoint.
    if not cloud_api:
        cloud_api = "https://api.knowco2.com"

    def esc_attr(val):
        try:
            return str(val).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
        except Exception:
            return ""

    # MQTT section
    mqtt_enabled = settings.get("mqtt_enabled", False)
    mqtt_broker = esc_attr(str(settings.get("mqtt_broker", "") or ""))
    mqtt_port = esc_attr(str(settings.get("mqtt_port", 1883) or 1883))
    mqtt_user = esc_attr(str(settings.get("mqtt_user", "") or ""))
    mqtt_topic_prefix = esc_attr(str(settings.get("mqtt_topic_prefix", "knowco2") or "knowco2"))
    mqtt_interval = esc_attr(str(settings.get("mqtt_interval_sec", 60) or 60))
    mqtt_checked = "checked" if mqtt_enabled else ""

    aio_enabled = settings.get("aio_enabled", False)
    aio_username = esc_attr(str(settings.get("aio_username", "") or ""))
    aio_group = esc_attr(str(settings.get("aio_group_key", "knowco2") or "knowco2"))
    aio_interval = esc_attr(str(settings.get("aio_interval_sec", 60) or 60))
    aio_checked = "checked" if aio_enabled else ""

    dim_enabled = settings.get("dim_enabled", False)
    dim_start = esc_attr(str(settings.get("dim_start_hour", 22) or 22))
    dim_end = esc_attr(str(settings.get("dim_end_hour", 7) or 7))
    dim_brightness = esc_attr(str(settings.get("dim_brightness", 10) or 10))
    dim_checked = "checked" if dim_enabled else ""

    current_lang = settings.get("lang", "en")

    tm = temp_mode
    dm = display_mode

    def sel_scale(opt):
        return "selected" if scale == opt else ""

    def sel_temp(opt):
        return "selected" if tm == opt else ""

    def sel_mode(opt_int):
        return "selected" if dm == opt_int else ""

    ip_for_hint = ip_str_cached or "192.168.4.1"
    mdns_hint = ""
    if wifi_mode == WIFI_MODE_STA and mdns_hostname:
        mdns_hint = f"<br><small class='muted'>On your home Wi-Fi, you can also use <span class='code'>http://{mdns_hostname}.local/</span>.</small>"

    # Build the settings page HTML.  If an admin password is configured, include it as a
    # hidden field named "pw" so that the password is preserved across form submissions.
    pw_hidden_field = ""
    try:
        _admin_pw = settings.get("admin_password", "")
        if _admin_pw:
            # Always HTML-escape the password value for safety.  We avoid importing
            # urllib here by manually replacing special characters that could break
            # the attribute.  The password should not contain quotes because the
            # input field for admin_pw is of type password.
            esc_pw = _admin_pw.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            # Embed the escaped password directly into the value attribute.  Use double
            # quotes around the value to avoid breaking the surrounding HTML.
            pw_hidden_field = "<input type=\"hidden\" name=\"pw\" value=\"" + esc_pw + "\">\n"
    except Exception:
        pw_hidden_field = ""

    html = """<!DOCTYPE html>
<html lang=\"""" + current_lang + """\" translate="yes">
<head>
  <meta charset="utf-8">
  <title>Know CO2 Settings</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    *,*::before,*::after{box-sizing:border-box}
    :root{--accent:#00bcd4;--accent-dark:#0097a7;--bg:#0b0b0b;--surface:#111;--border:#333;--text:#eee;--muted:#888;--danger:#e53935;--warn:#ffb300;--green:#4caf50;--radius:6px;--focus-ring:3px solid #00bcd4}
    html{font-size:16px;line-height:1.5}
    body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);margin:0;padding:0}
    .skip-link{position:absolute;top:-40px;left:0;background:var(--accent);color:#000;padding:8px 16px;text-decoration:none;border-radius:0 0 var(--radius) 0;font-weight:600;z-index:999}
    .skip-link:focus{top:0}
    .wrap{max-width:640px;margin:0 auto;padding:16px}
    h1{color:var(--accent);margin:8px 0 4px;font-size:1.5rem}
    h2{font-size:1rem;color:var(--muted);margin:0 0 12px;font-weight:400}
    fieldset{border:1px solid var(--border);border-radius:var(--radius);padding:12px 16px;margin:12px 0}
    legend{color:var(--accent);font-weight:600;font-size:0.9rem;padding:0 6px;text-transform:uppercase;letter-spacing:0.04em}
    label{display:block;margin-top:12px;font-size:0.95rem;color:var(--text)}
    label:first-child{margin-top:0}
    label span.lbl{display:block;margin-bottom:4px;font-weight:500}
    input[type=text],input[type=password],input[type=number],input[type=url],input[type=email],select,textarea{width:100%;padding:10px 12px;border-radius:var(--radius);border:1px solid var(--border);background:var(--surface);color:var(--text);font-size:1rem;font-family:inherit;transition:border-color .15s,box-shadow .15s;min-height:44px}
    input[type=text]:focus,input[type=password]:focus,input[type=number]:focus,input[type=url]:focus,input[type=email]:focus,select:focus,textarea:focus{outline:var(--focus-ring);outline-offset:2px;border-color:var(--accent)}
    input[type=checkbox]{width:18px;height:18px;accent-color:var(--accent);cursor:pointer;flex-shrink:0;margin-right:8px}
    .check-label{display:flex;align-items:flex-start;gap:8px;cursor:pointer;padding:4px 0;min-height:44px;align-items:center}
    .check-label input[type=checkbox]{margin-top:0}
    .help-text{font-size:0.8rem;color:var(--muted);margin-top:4px;line-height:1.4}
    button[type=submit],.btn{display:inline-flex;align-items:center;justify-content:center;min-height:48px;padding:10px 24px;border-radius:var(--radius);border:none;font-size:1rem;font-weight:600;cursor:pointer;transition:background .15s,transform .1s;letter-spacing:0.01em}
    button[type=submit]:focus,.btn:focus{outline:var(--focus-ring);outline-offset:2px}
    button[type=submit]:active,.btn:active{transform:scale(0.98)}
    .btn-primary{background:var(--accent);color:#000}
    .btn-primary:hover{background:var(--accent-dark)}
    .btn-danger{background:var(--danger);color:#fff}
    .btn-danger:hover{background:#c62828}
    .btn-block{width:100%;margin-top:16px}
    .code{font-family:'SFMono-Regular',Consolas,'Liberation Mono',Menlo,monospace;font-size:0.85rem;background:#1a1a1a;padding:2px 6px;border-radius:3px}
    .muted{color:var(--muted)}
    .warn-text{color:var(--warn)}
    .api-note{font-size:0.8rem;color:var(--muted);margin-top:8px;padding:8px;background:#1a1a1a;border-radius:var(--radius);border-left:3px solid var(--border)}
    a{color:var(--accent);text-decoration:none}
    a:hover{text-decoration:underline}
    a:focus{outline:var(--focus-ring);outline-offset:2px;border-radius:2px}
    .row{display:flex;gap:12px;flex-wrap:wrap}
    .row label{flex:1;min-width:140px}
    nav.page-nav{display:flex;gap:12px;flex-wrap:wrap;margin-top:16px;padding-top:12px;border-top:1px solid var(--border)}
    nav.page-nav a{font-size:0.9rem;padding:6px 12px;border-radius:var(--radius);border:1px solid var(--border);color:var(--muted)}
    nav.page-nav a:hover{color:var(--text);border-color:var(--muted)}
    .version-badge{font-size:0.75rem;font-weight:400;color:#555;vertical-align:middle;margin-left:4px}
    #chart-container{margin-top:12px;border:1px solid var(--border);border-radius:var(--radius);padding:8px}
    #chart{width:100%;max-width:420px;height:140px;background:#050505;border-radius:4px}
    #chart-debug{font-size:10px;color:#888;margin-top:4px}
    #status-card{border:1px solid var(--border);border-radius:var(--radius);padding:10px;margin:10px 0;background:var(--surface)}
    #status-main{font-size:18px;margin-bottom:6px}
    #status-extra{font-size:12px;color:#ccc}
    .badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;border:1px solid #444;margin-right:4px}
    .badge-low{border-color:#00c853;color:#00e676}
    .badge-med{border-color:#ffeb3b;color:#fff176}
    .badge-high{border-color:#ff5252;color:#ff8a80}
    @media(prefers-reduced-motion:reduce){*,*::before,*::after{transition:none!important;animation:none!important}}
    @media(max-width:480px){.wrap{padding:12px}.row{flex-direction:column}}
  </style>
</head>
<body>
  <a href="#main-content" class="skip-link" data-i18n="skip_nav">Skip to main content</a>
  <div class="wrap" id="main-content" role="main">
    <h1>Know CO2 <span class="version-badge">""" + FIRMWARE_VERSION + """</span></h1>
    <div class="muted" style="font-size:12px;margin-bottom:10px;">
      Open <span class="code">http://""" + ip_for_hint + """/</span>.""" + mdns_hint + """
      <br><small class="muted">If your phone says "No Internet", that’s expected during AP setup.</small>
    </div>

    <div id="status-card">
      <div id="status-main">CO₂: <span id="status-co2">--</span> ppm <span id="status-arrow" title="Trend (↑ rising, ↓ falling, → steady)">-</span> <span id="status-rate"></span></div>
      <div id="status-extra">
        Temp: <span id="status-temp">--.-</span>°
        &nbsp;·&nbsp; RH: <span id="status-rh">--.-</span>%
        <br>
        Air quality: <span id="status-quality" class="badge">unknown</span>
        <br>
        Device ID: <code id="status-device-id">""" + device_id + """</code>
        <div style="margin-top:8px;font-size:12px;color:#ccc;">
          <div>Battery: <span id="status-batt">--</span></div>
          <div>Pair code (setup): <code id="status-pair">--</code></div>
          <div>mDNS: <code id="status-mdns">--</code></div>
        </div>
      </div>
    </div>

    <div id="chart-container">
      <small class="muted">Live CO2 history (last """ + str(MAX_WEB_POINTS) + """ samples max)</small>
      <canvas id="chart" width="420" height="140"></canvas>
      <div id="chart-debug"></div>
    </div>

    <form method="POST" action="/">""" + pw_hidden_field + """
      <h2>CO2 &amp; Graph</h2>
      <fieldset>
        <legend>Thresholds</legend>
        <label for="field-low">
          <span class="lbl" data-i18n="lbl_low">Low threshold (ppm)</span>
          <input id="field-low" type="number" name="low" min="400" max="10000"
                 aria-describedby="help-low"
                 value='""" + str(int(settings.get("low_threshold", LOW_THRESHOLD_DEFAULT))) + """'>
          <span class="help-text" id="help-low" data-i18n="help_low">CO&#x2082; level below this is shown in green.</span>
        </label>
        <label for="field-med">
          <span class="lbl" data-i18n="lbl_med">Medium threshold (ppm)</span>
          <input id="field-med" type="number" name="med" min="400" max="10000"
                 aria-describedby="help-med"
                 value='""" + str(int(settings.get("med_threshold", MED_THRESHOLD_DEFAULT))) + """'>
          <span class="help-text" id="help-med" data-i18n="help_med">CO&#x2082; level below this is shown in yellow.</span>
        </label>
        <label for="field-alert">
          <span class="lbl" data-i18n="lbl_alert">Alert threshold (ppm)</span>
          <input id="field-alert" type="number" name="alert" min="400" max="10000"
                 aria-describedby="help-alert"
                 value='""" + str(int(settings.get("alert_threshold", ALERT_THRESHOLD_DEFAULT))) + """'>
          <span class="help-text" id="help-alert" data-i18n="help_alert">CO&#x2082; level at or above this triggers an alert.</span>
        </label>
        <label for="field-max-pts">
          <span class="lbl" data-i18n="lbl_max_pts">History buffer (samples)</span>
          <input id="field-max-pts" type="number" name="max_points" min="100" max="50000"
                 value='""" + str(int(max_points)) + """'>
        </label>
        <small>Higher values show longer history but use more memory.</small>
      </fieldset>

      <h2>Password protection</h2>
      <fieldset>
        <legend>Password</legend>
        <label for="field-admin-pw">
          <span class="lbl" data-i18n="lbl_admin_pw">Settings password</span>
          <input id="field-admin-pw" type="password" name="admin_pw" maxlength="64" value=""
                 aria-describedby="help-admin-pw">
          <span class="help-text" id="help-admin-pw" data-i18n="help_admin_pw">Leave blank to disable password protection.</span>
        </label>
        <small class="muted">Leave blank to disable password protection. When a password is set,
          the settings page will prompt you to log in with that password before changes can be made.</small>
      </fieldset>

      <fieldset>
        <legend data-i18n='sec_device'>Device</legend>
        <label><span data-i18n='lbl_lang'>Interface Language / Idioma / Sprache</span><br>
          <select name='lang'>
            """ + _i18n.build_lang_options(current_lang) + """
          </select>
        </label>
      </fieldset>

      <fieldset class="row">
        <legend>Graph scale</legend>
        <label>Scale mode
          <select name="scale">
            <option value="fixed" """ + sel_scale("fixed") + """>400-2000 ppm (tight)</option>
            <option value="wide" """ + sel_scale("wide") + """>400-3000 ppm (wide)</option>
            <option value="auto" """ + sel_scale("auto") + """>Automatic (based on data)</option>
          </select>
        </label>
      </fieldset>

      <fieldset>
        <legend>Alerts</legend>
        <label class="check-label">
          <input type="checkbox" name="alerts" value="on" """ + checked_alerts + """
                 data-i18n-aria="aria_alerts_check">
          <span data-i18n="lbl_alerts">Enable color alerts and on-screen alert messages</span>
        </label>
      </fieldset>

      <fieldset class="row">
        <legend data-i18n='sec_display'>Display &amp; Units</legend>
        <label>Temperature units
          <select name="temp_mode">
            <option value="F" """ + sel_temp("F") + """>Fahrenheit</option>
            <option value="C" """ + sel_temp("C") + """>Celsius</option>
          </select>
        </label>
        <label>Display mode
          <select name="mode">
            <option value="0" """ + sel_mode(0) + """>Text + temp &amp; humidity</option>
            <option value="1" """ + sel_mode(1) + """>Big CO2</option>
            <option value="2" """ + sel_mode(2) + """>Graph-only</option>
          </select>
        </label>
        <label class="check-label">
          <input type="checkbox" name="display_flip" id="display-flip"
                 data-i18n-aria="aria_flip_check" """ + ("checked" if settings.get("display_flip") else "") + """>
          <span data-i18n="lbl_flip">Flip display (upside-down mount)</span>
        </label>
        <span class="help-text" data-i18n="help_flip">Rotates the screen 180&#xB0; so the display reads correctly when the device is mounted upside down. Button functions are not affected.</span>
        <label class="check-label">
          <input type="checkbox" name="colorblind_mode" id="colorblind-mode" """ + ("checked" if settings.get("colorblind_mode") else "") + """>
          <span>Colorblind-friendly colors</span>
        </label>
        <span class="help-text">Replaces the red/yellow/green CO&#x2082; indicators with a blue/amber/vermillion palette (Wong colorblind-safe) that is distinguishable for deuteranopia, protanopia, and tritanopia. Takes effect immediately on save.</span>
      </fieldset>

      <h2>Wi-Fi Access Point</h2>
      <fieldset>
        <legend data-i18n='sec_wifi'>Local AP</legend>
        <label>AP SSID
          <input type="text" name="ap_ssid" maxlength="32"
                 value='""" + ap_ssid + """'>
        </label>
        <label>AP password
          <input type="password" name="ap_password" maxlength="63" value="">
        </label>

        <div class="row" style="margin-top:10px;">
          <button type="button" class="btn btn-primary" onclick="location.href='/?regen_ap=1'"
                  data-i18n="lbl_regen">
            Regenerate AP credentials
          </button>
          <div class="muted" style="margin-top:6px;">
            <small>This restarts AP. View the new password on the device (press D2).</small>
          </div>
        </div>
      </fieldset>

      <h2>Wi-Fi Network (client)</h2>
      <fieldset>
        <legend data-i18n="sec_wifi">For LAN + cloud uploads</legend>
        <p class="help-text" data-i18n="help_sta">Enter your home Wi-Fi credentials to connect the device to your network.</p>
        <label for="field-sta-ssid">
          <span class="lbl" data-i18n="lbl_sta_ssid">Network SSID</span>
          <input id="field-sta-ssid" type="text" name="sta_ssid" maxlength="32"
                 data-i18n-placeholder="ph_sta_ssid" placeholder="Your Wi-Fi network name"
                 value='""" + sta_ssid + """'>
        </label>
        <label for="field-sta-pass">
          <span class="lbl" data-i18n="lbl_sta_pass">Network password</span>
          <input id="field-sta-pass" type="password" name="sta_password" maxlength="63" value=""
                 data-i18n-placeholder="ph_sta_pass" placeholder="Your Wi-Fi password">
        </label>
        <small class="muted">
          Tip: after saving STA credentials, <b>hold D2 for ~2 seconds</b> to switch into STA mode.
        </small>
      </fieldset>

      <h2>Cloud telemetry</h2>
      <fieldset>
        <legend data-i18n='sec_cloud'>API data ingest</legend>
        <p class="help-text" data-i18n="help_cloud">Send CO&#x2082; readings to the Know CO&#x2082; cloud dashboard.</p>
        <small class="muted">Onboard your device at <a href=\"https://cloud.knowco2.com\">https://cloud.knowco2.com</a> register and generate a device id and secret to enter.</small>
        <label class="check-label">
          <input type="checkbox" name="cloud_enabled" value="on" """ + cloud_enabled_checked + """
                 data-i18n-aria="aria_cloud_check">
          <span data-i18n="lbl_cloud_en">Enable cloud uploads (requires STA Wi-Fi + token)</span>
        </label>

        <label for="field-cloud-url">
          <span class="lbl" data-i18n="lbl_cloud_url">Cloud API URL</span>
          <input id="field-cloud-url" type="text" name="cloud_api_url" maxlength="200"
                 data-i18n-placeholder="ph_cloud_url" placeholder="https://api.knowco2.com/v1/ingest"
                 value='""" + cloud_api + """'>
        </label>

        <label for="field-cloud-token">
          <span class="lbl" data-i18n="lbl_cloud_token">Device token (secret)</span>
          <input id="field-cloud-token" type="password" name="cloud_device_token" maxlength="128"
                 data-i18n-placeholder="ph_cloud_token" placeholder="Paste your device token here" value="">
          <span class="help-text" data-i18n="lbl_cloud_secret">Device secret / token</span>
        </label>
        <small>Paste token once. It is stored on device and not shown again.</small>

        <label for="field-device-id">
          <span class="lbl" data-i18n="lbl_device_id">Device ID</span>
          <input id="field-device-id" type="text" name="device_id" maxlength="40"
                 data-i18n-placeholder="ph_device_id" placeholder="co2-node-1"
                 aria-describedby="help-device-id"
                 value='""" + device_id + """'>
          <span class="help-text" id="help-device-id" data-i18n="help_device_id">Identifier sent with cloud and MQTT data.</span>
        </label>

        <label for="field-cloud-interval">
          <span class="lbl" data-i18n="lbl_cloud_interval">Upload interval (seconds)</span>
          <input id="field-cloud-interval" type="number" name="cloud_interval_sec" min="15" max="3600"
                 value='""" + str(int(settings.get("cloud_interval_sec", 60))) + """'>
        </label>
        <small class="muted">
          Pairing: create an account, then enter this device's <b>Pair code</b>.
          The cloud app returns a device token you paste here.
        </small>
      </fieldset>

      <!-- Device identity section removed; Device ID is now under Cloud telemetry and local endpoints moved to bottom. -->

      <fieldset>
        <legend data-i18n='sec_mqtt'>MQTT Broker (Home Assistant etc.)</legend>
        <p class="help-text" data-i18n="help_mqtt">Publish readings to a local MQTT broker (e.g. Home Assistant).</p>
        <label class="check-label">
          <input type='checkbox' name='mqtt_enabled' """ + mqtt_checked + """
                 data-i18n-aria="aria_mqtt_check">
          <span data-i18n="lbl_mqtt_en">Enable MQTT publishing</span>
        </label>
        <label for="field-mqtt-broker">
          <span class="lbl" data-i18n="lbl_mqtt_broker">Broker hostname/IP</span>
          <input id="field-mqtt-broker" type='text' name='mqtt_broker' value='""" + mqtt_broker + """'
                 data-i18n-placeholder="ph_mqtt_broker" placeholder='192.168.1.x or mqtt.example.com'>
        </label>
        <label for="field-mqtt-port">
          <span class="lbl" data-i18n="lbl_mqtt_port">Port</span>
          <input id="field-mqtt-port" type='number' name='mqtt_port' value='""" + mqtt_port + """' min='1' max='65535'>
        </label>
        <label for="field-mqtt-user">
          <span class="lbl" data-i18n="lbl_mqtt_user">Username (optional)</span>
          <input id="field-mqtt-user" type='text' name='mqtt_user' value='""" + mqtt_user + """'>
        </label>
        <label for="field-mqtt-pass">
          <span class="lbl" data-i18n="lbl_mqtt_pass">Password (optional)</span>
          <input id="field-mqtt-pass" type='password' name='mqtt_pass' placeholder='leave blank to keep current'>
        </label>
        <label for="field-mqtt-prefix">
          <span class="lbl" data-i18n="lbl_mqtt_prefix">Topic prefix</span>
          <input id="field-mqtt-prefix" type='text' name='mqtt_topic_prefix' value='""" + mqtt_topic_prefix + """'
                 data-i18n-placeholder="ph_mqtt_prefix" placeholder='knowco2'>
        </label>
        <label for="field-mqtt-interval">
          <span class="lbl" data-i18n="lbl_mqtt_interval">Publish interval (seconds)</span>
          <input id="field-mqtt-interval" type='number' name='mqtt_interval_sec' value='""" + mqtt_interval + """' min='15' max='3600'>
        </label>
        <small>Topics: &lt;prefix&gt;/co2, &lt;prefix&gt;/temp_c, &lt;prefix&gt;/rh</small>
      </fieldset>
      <fieldset>
        <legend data-i18n='sec_aio'>Adafruit IO</legend>
        <p class="help-text" data-i18n="help_aio">Publish readings to Adafruit IO feeds.</p>
        <label class="check-label">
          <input type='checkbox' name='aio_enabled' """ + aio_checked + """
                 data-i18n-aria="aria_aio_check">
          <span data-i18n="lbl_aio_en">Enable Adafruit IO</span>
        </label>
        <label for="field-aio-user">
          <span class="lbl" data-i18n="lbl_aio_user">AIO Username</span>
          <input id="field-aio-user" type='text' name='aio_username' value='""" + aio_username + """'>
        </label>
        <label for="field-aio-key">
          <span class="lbl">AIO Key</span>
          <input id="field-aio-key" type='password' name='aio_key' placeholder='leave blank to keep current'>
        </label>
        <label for="field-aio-group">
          <span class="lbl" data-i18n="lbl_aio_group">Feed group key</span>
          <input id="field-aio-group" type='text' name='aio_group_key' value='""" + aio_group + """'
                 data-i18n-placeholder="ph_aio_group" placeholder='knowco2'>
        </label>
        <label for="field-aio-interval">
          <span class="lbl" data-i18n="lbl_aio_interval">Publish interval (seconds)</span>
          <input id="field-aio-interval" type='number' name='aio_interval_sec' value='""" + aio_interval + """' min='15' max='3600'>
        </label>
        <small>Feeds: &lt;group&gt;.co2, &lt;group&gt;.temperature, &lt;group&gt;.humidity</small>
      </fieldset>
      <fieldset>
        <legend data-i18n='sec_dim'>Display Dimming Schedule</legend>
        <p class="help-text" data-i18n="help_dim">Automatically reduce display brightness during set hours. Requires NTP time sync.</p>
        <label class="check-label">
          <input type='checkbox' name='dim_enabled' """ + dim_checked + """
                 data-i18n-aria="aria_dim_check">
          <span data-i18n="lbl_dim_en">Enable scheduled dimming (requires NTP)</span>
        </label>
        <label for="field-dim-start">
          <span class="lbl" data-i18n="lbl_dim_start">Dim start hour (0-23)</span>
          <input id="field-dim-start" type='number' name='dim_start_hour' value='""" + dim_start + """' min='0' max='23'>
        </label>
        <label for="field-dim-end">
          <span class="lbl" data-i18n="lbl_dim_end">Dim end hour (0-23)</span>
          <input id="field-dim-end" type='number' name='dim_end_hour' value='""" + dim_end + """' min='0' max='23'>
        </label>
        <label for="field-dim-bright">
          <span class="lbl" data-i18n="lbl_dim_bright">Brightness during dim period (0-100%)</span>
          <input id="field-dim-bright" type='number' name='dim_brightness' value='""" + dim_brightness + """' min='0' max='100'>
        </label>
        <small>Example: start=22, end=7 dims from 10 PM to 7 AM.</small>
      </fieldset>

      <div class="row">
        <button type="submit" class="btn btn-primary btn-block" data-i18n="save">Save settings</button>
      </div>
      <div class="row">
        <small>
          <b>Local endpoints:</b><br>
          • <code>GET /status</code> → live JSON status<br>
          • <code>GET /data</code> → CO₂ history JSON (up to """ + str(MAX_WEB_POINTS) + """ points)<br>
          • <code>GET /export.csv</code> → download CO₂ history as CSV
        </small>
      </div>
      <div class="row muted">
        <small>Settings are saved to <code>settings.json</code>. If you see "USB mode: settings won't save", eject CIRCUITPY from your computer.</small>
      </div>
      <nav class="page-nav" aria-label="Page navigation">
        <a href="/calibration" data-i18n="nav_calib">Calibration</a>
        <a href="/update" data-i18n="nav_ota" style="color:var(--danger)">Firmware Update</a>
      </nav>
    </form>
  </div>
  <script>
    let lastStatus = null;
    const SAMPLE_PERIOD_SEC = 5;
    const initialPoints = """ + initial_json + """;
    const CB_MODE = """ + web_cb_mode + """;

    // Color palettes — matches the device firmware schemes exactly.
    const PAL_NORMAL = { low: '#00e676', med: '#fff176', alert: '#ff5252',
                         zoneLow: '#00e676', zoneMed: '#fff176', zoneHigh: '#ff5252' };
    const PAL_CB     = { low: '#56B4E9', med: '#E69F00', alert: '#D55E00',
                         zoneLow: '#56B4E9', zoneMed: '#E69F00', zoneHigh: '#D55E00' };
    const PAL = CB_MODE ? PAL_CB : PAL_NORMAL;

    const canvas = document.getElementById('chart');
    const ctx   = canvas.getContext('2d');
    const debugEl = document.getElementById('chart-debug');

    const statusCo2El = document.getElementById('status-co2');
    const statusArrowEl = document.getElementById('status-arrow');
    const statusTempEl = document.getElementById('status-temp');
    const statusRhEl = document.getElementById('status-rh');
    const statusQualityEl = document.getElementById('status-quality');
    const statusDeviceEl = document.getElementById('status-device-id');
    const statusBattEl  = document.getElementById('status-batt');
    const statusPairEl  = document.getElementById('status-pair');
    const statusMdnsEl  = document.getElementById('status-mdns');
    const statusRateEl  = document.getElementById('status-rate');

    function drawChart(points) {
      const w = canvas.width;
      const h = canvas.height;

      const padL = 42;   // wider left pad for Y-axis tick labels
      const padR = 8;
      const padT = 14;
      const padB = 24;   // taller bottom pad for X-axis time labels
      const cw = w - padL - padR;
      const ch = h - padT - padB;

      ctx.fillStyle = '#050505';
      ctx.fillRect(0, 0, w, h);

      if (!points || points.length === 0) {
        ctx.fillStyle = '#aaaaaa';
        ctx.font = '13px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('No data yet', w/2, h/2);
        ctx.textAlign = 'left';
        debugEl.textContent = 'Samples: 0';
        return;
      }

      const lowT   = (lastStatus && typeof lastStatus.low_threshold   === 'number') ? lastStatus.low_threshold   : 800;
      const medT   = (lastStatus && typeof lastStatus.med_threshold   === 'number') ? lastStatus.med_threshold   : 1200;
      const alertT = (lastStatus && typeof lastStatus.alert_threshold === 'number') ? lastStatus.alert_threshold : 1500;

      let min = Math.min.apply(null, points);
      let max = Math.max.apply(null, points);
      min = Math.min(min, 400, lowT);
      max = Math.max(max, 800, alertT + 100);
      if (min === max) { min -= 50; max += 50; }

      function yFor(v) {
        const t = (v - min) / (max - min);
        return padT + (1 - t) * ch;
      }
      function xFor(i) {
        const denom = Math.max(points.length - 1, 1);
        return padL + (i / denom) * cw;
      }
      function segColor(v) {
        if (v < lowT)   return PAL.low;
        if (v < medT)   return PAL.med;
        if (v < alertT) return PAL.alert;
        return PAL.alert;
      }

      // ── Zone background fills ─────────────────────────────────────────
      ctx.globalAlpha = 0.08;
      ctx.fillStyle = PAL.zoneLow;
      ctx.fillRect(padL, yFor(lowT),   cw, yFor(min)   - yFor(lowT));
      ctx.fillStyle = PAL.zoneMed;
      ctx.fillRect(padL, yFor(medT),   cw, yFor(lowT)  - yFor(medT));
      ctx.fillStyle = PAL.zoneHigh;
      ctx.fillRect(padL, yFor(padT),   cw, yFor(medT)  - yFor(padT));
      ctx.globalAlpha = 1.0;

      // ── Y-axis ticks and horizontal grid lines ────────────────────────
      const yTicks = [];
      const candidates = [min, lowT, medT, alertT, max];
      for (let i = 0; i < candidates.length; i++) {
        const v = candidates[i];
        if (yTicks.every(u => Math.abs(u - v) > 20)) yTicks.push(v);
      }

      ctx.font = '10px monospace';
      ctx.textAlign = 'right';
      for (let i = 0; i < yTicks.length; i++) {
        const v  = yTicks[i];
        const yy = yFor(v);
        // Horizontal grid line
        ctx.strokeStyle = '#1e1e1e';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(padL, yy);
        ctx.lineTo(padL + cw, yy);
        ctx.stroke();
        // Threshold dashes coloured by zone
        if (v === lowT || v === medT || v === alertT) {
          ctx.strokeStyle = (v === lowT ? PAL.low : v === medT ? PAL.med : PAL.alert);
          ctx.setLineDash([4, 4]);
          ctx.lineWidth = 1;
          ctx.beginPath(); ctx.moveTo(padL, yy); ctx.lineTo(padL + cw, yy); ctx.stroke();
          ctx.setLineDash([]);
        }
        // Y-axis label
        const color = (v === lowT ? PAL.low : v === medT ? PAL.med : v === alertT ? PAL.alert : '#666666');
        ctx.fillStyle = color;
        ctx.fillText(v.toFixed(0), padL - 5, yy + 3);
      }
      // "ppm" Y-axis unit label rotated vertically
      ctx.save();
      ctx.translate(10, padT + ch / 2);
      ctx.rotate(-Math.PI / 2);
      ctx.font = '9px sans-serif';
      ctx.fillStyle = '#555555';
      ctx.textAlign = 'center';
      ctx.fillText('ppm', 0, 0);
      ctx.restore();
      ctx.textAlign = 'left';

      // ── Axis border lines ─────────────────────────────────────────────
      ctx.strokeStyle = '#555555';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(padL, padT);
      ctx.lineTo(padL, padT + ch);          // Y-axis
      ctx.lineTo(padL + cw, padT + ch);     // X-axis
      ctx.stroke();

      // ── Data line ────────────────────────────────────────────────────
      ctx.lineWidth = 2;
      for (let i = 1; i < points.length; i++) {
        const v1 = points[i];
        ctx.strokeStyle = segColor(v1);
        ctx.beginPath();
        ctx.moveTo(xFor(i-1), yFor(points[i-1]));
        ctx.lineTo(xFor(i),   yFor(v1));
        ctx.stroke();
      }

      // ── X-axis time labels ────────────────────────────────────────────
      const spanSec = (points.length - 1) * SAMPLE_PERIOD_SEC;
      function fmtTime(sec) {
        if (sec < 90) return '-' + sec.toFixed(0) + 's';
        return '-' + (sec / 60).toFixed(1) + 'm';
      }
      ctx.font = '10px monospace';
      ctx.fillStyle = '#888888';
      ctx.textAlign = 'left';
      ctx.fillText(fmtTime(spanSec), padL + 2, padT + ch + 14);   // left = oldest
      ctx.textAlign = 'center';
      ctx.fillText(fmtTime(spanSec / 2), padL + cw / 2, padT + ch + 14);  // mid
      ctx.textAlign = 'right';
      ctx.fillText('now', padL + cw - 2, padT + ch + 14);          // right = newest
      ctx.textAlign = 'left';

      // Latest-point dot
      if (points.length > 0) {
        const lx = xFor(points.length - 1);
        const ly = yFor(points[points.length - 1]);
        ctx.fillStyle = '#ffffff';
        ctx.beginPath();
        ctx.arc(lx, ly, 3, 0, 2 * Math.PI);
        ctx.fill();
      }

      debugEl.textContent = 'Samples: ' + points.length + ' · span: ' + (spanSec/60).toFixed(1) + 'm';
    }

    function updateStatusCard(s) {
      lastStatus = s;
      if (!s) return;
      statusCo2El.textContent = (typeof s.co2 === 'number') ? s.co2.toFixed(0) : '--';
      statusArrowEl.textContent = s.trend_arrow || '-';
      // Show rate of change if provided: format with sign and one decimal place (ppm/s)
      if (typeof s.rate_of_change === 'number') {
        const rc = s.rate_of_change;
        statusRateEl.textContent = (rc >= 0 ? '+' : '') + rc.toFixed(1) + ' ppm/s';
      } else {
        statusRateEl.textContent = '';
      }
      statusTempEl.textContent = (typeof s.temp_display === 'number') ? s.temp_display.toFixed(1) : '--.-';
      statusRhEl.textContent = (typeof s.rh === 'number') ? s.rh.toFixed(1) : '--.-';
      statusDeviceEl.textContent = s.device_id || 'co2-node';
      statusPairEl.textContent = s.pair_code || '--';
      statusMdnsEl.textContent = s.mdns || '--';

      let badgeClass = 'badge';
      let label = 'unknown';
      if (typeof s.co2 === 'number') {
        if (s.co2 < s.low_threshold) { badgeClass += ' badge-low'; label = 'Low CO₂'; }
        else if (s.co2 < s.med_threshold) { badgeClass += ' badge-med'; label = 'Medium CO₂'; }
        else { badgeClass += ' badge-high'; label = 'High CO₂'; }
      }
      statusQualityEl.className = badgeClass;
      statusQualityEl.textContent = label;

      if (typeof s.battery_v === 'number' && typeof s.battery_pct === 'number') {
        statusBattEl.textContent = s.battery_v.toFixed(2) + 'V (' + s.battery_pct.toFixed(0) + '%)';
      } else if (typeof s.battery_pct === 'number') {
        statusBattEl.textContent = s.battery_pct.toFixed(0) + '%';
      } else {
        statusBattEl.textContent = '--';
      }
    }

    function refreshChartFromServer() {
      const xhr = new XMLHttpRequest();
      xhr.open('GET', '/data', true);
      xhr.onreadystatechange = function() {
        if (xhr.readyState === 4) {
          if (xhr.status === 200) {
            try {
              const data = JSON.parse(xhr.responseText);
              const pts = (data && data.co2) ? data.co2 : [];
              drawChart(pts);
            } catch (e) {
              debugEl.textContent = 'Error parsing /data';
            }
          } else {
            debugEl.textContent = 'HTTP ' + xhr.status;
          }
        }
      };
      xhr.send();
    }

    function refreshStatusFromServer() {
      const xhr = new XMLHttpRequest();
      xhr.open('GET', '/status', true);
      xhr.onreadystatechange = function() {
        if (xhr.readyState === 4 && xhr.status === 200) {
          try { updateStatusCard(JSON.parse(xhr.responseText)); } catch (e) {}
        }
      };
      xhr.send();
    }

    drawChart(initialPoints);
    refreshStatusFromServer();
    setInterval(refreshChartFromServer, 5000);
    setInterval(refreshStatusFromServer, 5000);
  </script>
  <p style='color:#666;font-size:12px;margin-top:12px' data-i18n='note_https'>Note: Local web server uses HTTP (HTTPS not supported in CircuitPython). Traffic stays on your LAN.</p>
<script>
""" + _i18n.build_translations_js() + """
function applyLang(lang){
  var t = T[lang] || T['en'];
  // Update the document language for screen readers
  document.documentElement.lang = lang;
  // Update the page <title>
  if (t.title) document.title = t.title;
  // Update text nodes
  document.querySelectorAll('[data-i18n]').forEach(function(el){
    var k = el.getAttribute('data-i18n');
    if (!t[k]) return;
    if (el.children.length === 0) {
      el.textContent = t[k];
    } else {
      for (var i = 0; i < el.childNodes.length; i++) {
        if (el.childNodes[i].nodeType === 3) {
          el.childNodes[i].nodeValue = t[k];
          break;
        }
      }
    }
  });
  // Update placeholder attributes
  document.querySelectorAll('[data-i18n-placeholder]').forEach(function(el){
    var k = el.getAttribute('data-i18n-placeholder');
    if (t[k]) el.placeholder = t[k];
  });
  // Update aria-label attributes
  document.querySelectorAll('[data-i18n-aria]').forEach(function(el){
    var k = el.getAttribute('data-i18n-aria');
    if (t[k]) el.setAttribute('aria-label', t[k]);
  });
  // Keep language selector in sync
  var sel = document.querySelector('select[name=lang]');
  if (sel) sel.value = lang;
  localStorage.setItem('kco2_lang', lang);
}
(function(){
  var saved = localStorage.getItem('kco2_lang') || '""" + current_lang + """';
  var sel = document.querySelector('select[name=lang]');
  if (sel) sel.addEventListener('change', function(){ applyLang(this.value); });
  applyLang(saved);
})();
</script>
</body>
</html>"""
    return html
