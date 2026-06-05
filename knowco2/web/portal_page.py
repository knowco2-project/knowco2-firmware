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
<html lang=\"""" + current_lang + """\">
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
            <option value='en'""" + (" selected" if current_lang=="en" else "") + """>English</option>
            <option value='es'""" + (" selected" if current_lang=="es" else "") + """>Espa&#241;ol</option>
            <option value='fr'""" + (" selected" if current_lang=="fr" else "") + """>Fran&#231;ais</option>
            <option value='de'""" + (" selected" if current_lang=="de" else "") + """>Deutsch</option>
            <option value='pt'""" + (" selected" if current_lang=="pt" else "") + """>Portugu&#234;s</option>
            <option value='it'""" + (" selected" if current_lang=="it" else "") + """>Italiano</option>
            <option value='ja'""" + (" selected" if current_lang=="ja" else "") + """>\u65e5\u672c\u8a9e</option>
            <option value='zh'""" + (" selected" if current_lang=="zh" else "") + """>\u4e2d\u6587(\u7b80\u4f53)</option>
            <option value='ko'""" + (" selected" if current_lang=="ko" else "") + """>\ud55c\uad6d\uc5b4</option>
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
// ---- Know CO2 Web UI i18n ----
// Translations for key UI strings.  Applied client-side so no device reboot needed.
// Language preference is stored in localStorage and also sent to device on form save.
var T = {
  en: {
    title:"Know CO\u2082 Settings",save:"Save Settings",
    sec_display:"Display & Thresholds",sec_wifi:"Wi-Fi",
    sec_cloud:"Cloud Upload",sec_mqtt:"MQTT Broker",
    sec_aio:"Adafruit IO",sec_dim:"Display Dimming",
    sec_device:"Device",sec_calib:"Calibration",
    lbl_low:"Low threshold (ppm)",lbl_med:"Medium threshold (ppm)",
    lbl_alert:"Alert threshold (ppm)",lbl_temp:"Temperature unit",
    lbl_alerts:"Enable alerts",lbl_scale:"Graph scale",
    lbl_ap_ssid:"AP network name",lbl_ap_pass:"AP password",
    lbl_sta_ssid:"Home Wi-Fi SSID",lbl_sta_pass:"Home Wi-Fi password",
    lbl_cloud_url:"API URL",lbl_cloud_token:"Device token",
    lbl_cloud_interval:"Upload interval (seconds)",lbl_cloud_en:"Enable cloud upload",
    lbl_mqtt_en:"Enable MQTT publishing",lbl_mqtt_broker:"Broker hostname/IP",
    lbl_aio_en:"Enable Adafruit IO",lbl_aio_user:"AIO Username",
    lbl_dim_en:"Enable scheduled dimming (requires NTP)",
    lbl_dim_start:"Dim start hour (0-23)",lbl_dim_end:"Dim end hour (0-23)",
    lbl_dim_bright:"Brightness during dim period (0-100%)",
    lbl_lang:"Interface Language",lbl_admin_pw:"Settings password",
    lbl_device_id:"Device ID",btn_ota:"OTA Firmware Update",
    note_dim:"Example: start=22, end=7 dims from 10 PM to 7 AM.",
    note_https:"Note: Local web server uses HTTP (HTTPS not supported in CircuitPython). Traffic stays on your LAN.",
    help_low:"CO\u2082 level below this is shown in green.",
    help_med:"CO\u2082 level below this is shown in yellow.",
    help_alert:"CO\u2082 level at or above this triggers an alert.",
    help_device_id:"Identifier sent with cloud and MQTT data.",
    help_admin_pw:"Leave blank to disable password protection.",
    help_sta:"Enter your home Wi-Fi credentials to connect the device to your network.",
    help_cloud:"Send CO\u2082 readings to the Know CO\u2082 cloud dashboard.",
    help_mqtt:"Publish readings to a local MQTT broker (e.g. Home Assistant).",
    help_aio:"Publish readings to Adafruit IO feeds.",
    help_dim:"Automatically reduce display brightness during set hours. Requires NTP time sync.",
    help_ota:"Download and install new firmware. The device will reboot after a successful update.",
    lbl_regen:"Regenerate AP credentials",
    lbl_sta_connect:"Connect to this network on next reboot",
    lbl_mode_text:"Text",lbl_mode_big:"Big CO\u2082",lbl_mode_graph:"Graph",
    lbl_scale_fixed:"400\u20132000 ppm (tight)",lbl_scale_wide:"400\u20133000 ppm (wide)",lbl_scale_auto:"Automatic",
    lbl_max_pts:"History buffer (samples)",
    lbl_cloud_secret:"Device secret / token",
    lbl_mqtt_port:"Port",lbl_mqtt_user:"Username (optional)",lbl_mqtt_pass:"Password (optional)",
    lbl_mqtt_prefix:"Topic prefix",lbl_mqtt_interval:"Publish interval (seconds)",
    lbl_aio_group:"Feed group key",lbl_aio_interval:"Publish interval (seconds)",
    nav_calib:"Calibration",nav_back:"Back to Settings",nav_ota:"Firmware Update",
    ph_device_id:"co2-node-1",ph_sta_ssid:"Your Wi-Fi network name",ph_sta_pass:"Your Wi-Fi password",
    ph_cloud_url:"https://api.knowco2.com/v1/ingest",ph_cloud_token:"Paste your device token here",
    ph_mqtt_broker:"192.168.1.x or mqtt.example.com",ph_mqtt_prefix:"knowco2",
    ph_aio_user:"your-adafruit-username",ph_aio_group:"knowco2",
    ph_fw_url:"http://192.168.1.x/firmware.py",
    aria_alerts_check:"Toggle CO\u2082 alert notifications",
    aria_cloud_check:"Toggle cloud data upload",
    aria_mqtt_check:"Toggle MQTT publishing",
    aria_aio_check:"Toggle Adafruit IO publishing",
    aria_dim_check:"Toggle scheduled display dimming",
    lbl_flip:"Flip display (upside-down mount)",
    help_flip:"Rotates the screen 180\u00b0 so it reads correctly when mounted upside down. Buttons are not affected.",
    aria_flip_check:"Toggle display orientation flip",
    skip_nav:"Skip to main content"
  },
  es: {
    title:"Ajustes Know CO\u2082",save:"Guardar ajustes",
    sec_display:"Pantalla y umbrales",sec_wifi:"Wi-Fi",
    sec_cloud:"Subida a la nube",sec_mqtt:"Broker MQTT",
    sec_aio:"Adafruit IO",sec_dim:"Atenuacion de pantalla",
    sec_device:"Dispositivo",sec_calib:"Calibracion",
    lbl_low:"Umbral bajo (ppm)",lbl_med:"Umbral medio (ppm)",
    lbl_alert:"Umbral de alerta (ppm)",lbl_temp:"Unidad de temperatura",
    lbl_alerts:"Activar alertas",lbl_scale:"Escala del grafico",
    lbl_ap_ssid:"Nombre de red AP",lbl_ap_pass:"Contrasena AP",
    lbl_sta_ssid:"SSID Wi-Fi del hogar",lbl_sta_pass:"Contrasena Wi-Fi",
    lbl_cloud_url:"URL de la API",lbl_cloud_token:"Token del dispositivo",
    lbl_cloud_interval:"Intervalo de subida (segundos)",lbl_cloud_en:"Activar subida a la nube",
    lbl_mqtt_en:"Activar publicacion MQTT",lbl_mqtt_broker:"Servidor/IP del broker",
    lbl_aio_en:"Activar Adafruit IO",lbl_aio_user:"Usuario AIO",
    lbl_dim_en:"Activar atenuacion programada (requiere NTP)",
    lbl_dim_start:"Hora inicio (0-23)",lbl_dim_end:"Hora fin (0-23)",
    lbl_dim_bright:"Brillo durante atenuacion (0-100%)",
    lbl_lang:"Idioma de la interfaz",lbl_admin_pw:"Contrasena de ajustes",
    lbl_device_id:"ID del dispositivo",btn_ota:"Actualizar firmware (OTA)",
    note_dim:"Ejemplo: inicio=22, fin=7 atenua de 22:00 a 07:00.",
    note_https:"Nota: El servidor web usa HTTP. El trafico permanece en su red local.",
    help_low:"El nivel de CO\u2082 por debajo de este se muestra en verde.",
    help_med:"El nivel de CO\u2082 por debajo de este se muestra en amarillo.",
    help_alert:"El nivel de CO\u2082 igual o superior a este activa una alerta.",
    help_device_id:"Identificador enviado con los datos de nube y MQTT.",
    help_admin_pw:"Dejar en blanco para desactivar la proteccion por contrasena.",
    help_sta:"Introduce las credenciales de tu Wi-Fi para conectar el dispositivo.",
    help_cloud:"Envia lecturas de CO\u2082 al panel de Know CO\u2082.",
    help_mqtt:"Publica lecturas en un broker MQTT local (p. ej. Home Assistant).",
    help_aio:"Publica lecturas en feeds de Adafruit IO.",
    help_dim:"Reduce automaticamente el brillo en las horas configuradas. Requiere NTP.",
    help_ota:"Descarga e instala nuevo firmware. El dispositivo se reiniciara.",
    lbl_regen:"Regenerar credenciales AP",lbl_sta_connect:"Conectar en el proximo reinicio",
    lbl_mode_text:"Texto",lbl_mode_big:"CO\u2082 grande",lbl_mode_graph:"Grafico",
    lbl_scale_fixed:"400\u20132000 ppm (ajustado)",lbl_scale_wide:"400\u20133000 ppm (amplio)",lbl_scale_auto:"Automatico",
    lbl_max_pts:"Buffer de historial (muestras)",lbl_cloud_secret:"Secreto/token del dispositivo",
    lbl_mqtt_port:"Puerto",lbl_mqtt_user:"Usuario (opcional)",lbl_mqtt_pass:"Contrasena (opcional)",
    lbl_mqtt_prefix:"Prefijo del topic",lbl_mqtt_interval:"Intervalo de publicacion (segundos)",
    lbl_aio_group:"Clave de grupo de feeds",lbl_aio_interval:"Intervalo de publicacion (segundos)",
    nav_calib:"Calibracion",nav_back:"Volver a Ajustes",nav_ota:"Actualizar firmware",
    ph_device_id:"co2-node-1",ph_sta_ssid:"Nombre de tu red Wi-Fi",ph_sta_pass:"Contrasena Wi-Fi",
    ph_cloud_url:"https://api.knowco2.com/v1/ingest",ph_cloud_token:"Pega tu token aqui",
    ph_mqtt_broker:"192.168.1.x o mqtt.ejemplo.com",ph_mqtt_prefix:"knowco2",
    ph_aio_user:"tu-usuario-adafruit",ph_aio_group:"knowco2",
    ph_fw_url:"http://192.168.1.x/firmware.py",
    aria_alerts_check:"Activar o desactivar alertas de CO\u2082",
    aria_cloud_check:"Activar o desactivar subida de datos a la nube",
    aria_mqtt_check:"Activar o desactivar publicacion MQTT",
    aria_aio_check:"Activar o desactivar publicacion en Adafruit IO",
    aria_dim_check:"Activar o desactivar atenuacion programada",
    lbl_flip:"Flip display (upside-down mount)",
    help_flip:"Rotates the screen 180\u00b0 so it reads correctly when mounted upside down. Buttons are not affected.",
    aria_flip_check:"Toggle display orientation flip",
    skip_nav:"Saltar al contenido principal"
  },
  fr: {
    title:"Parametres Know CO\u2082",save:"Enregistrer",
    sec_display:"Affichage et seuils",sec_wifi:"Wi-Fi",
    sec_cloud:"Envoi vers le cloud",sec_mqtt:"Broker MQTT",
    sec_aio:"Adafruit IO",sec_dim:"Attenuation ecran",
    sec_device:"Appareil",sec_calib:"Calibration",
    lbl_low:"Seuil bas (ppm)",lbl_med:"Seuil moyen (ppm)",
    lbl_alert:"Seuil d'alerte (ppm)",lbl_temp:"Unite de temperature",
    lbl_alerts:"Activer les alertes",lbl_scale:"Echelle du graphique",
    lbl_ap_ssid:"Nom du reseau AP",lbl_ap_pass:"Mot de passe AP",
    lbl_sta_ssid:"SSID Wi-Fi domicile",lbl_sta_pass:"Mot de passe Wi-Fi",
    lbl_cloud_url:"URL de l'API",lbl_cloud_token:"Token de l'appareil",
    lbl_cloud_interval:"Intervalle d'envoi (secondes)",lbl_cloud_en:"Activer l'envoi cloud",
    lbl_mqtt_en:"Activer la publication MQTT",lbl_mqtt_broker:"Hote/IP du broker",
    lbl_aio_en:"Activer Adafruit IO",lbl_aio_user:"Utilisateur AIO",
    lbl_dim_en:"Activer l'attenuation programmee (NTP requis)",
    lbl_dim_start:"Heure debut (0-23)",lbl_dim_end:"Heure fin (0-23)",
    lbl_dim_bright:"Luminosite en periode d'attenuation (0-100%)",
    lbl_lang:"Langue de l'interface",lbl_admin_pw:"Mot de passe des parametres",
    lbl_device_id:"ID de l'appareil",btn_ota:"Mise a jour OTA",
    note_dim:"Exemple: debut=22, fin=7 attenue de 22h a 7h.",
    note_https:"Note: Le serveur web utilise HTTP. Le trafic reste sur votre reseau local.",
    help_low:"Le niveau de CO\u2082 en dessous est affiche en vert.",
    help_med:"Le niveau de CO\u2082 en dessous est affiche en jaune.",
    help_alert:"Le niveau de CO\u2082 egal ou superieur declenche une alerte.",
    help_device_id:"Identifiant envoye avec les donnees cloud et MQTT.",
    help_admin_pw:"Laisser vide pour desactiver la protection par mot de passe.",
    help_sta:"Entrez vos identifiants Wi-Fi pour connecter l'appareil.",
    help_cloud:"Envoyez les mesures de CO\u2082 au tableau de bord Know CO\u2082.",
    help_mqtt:"Publiez les mesures sur un broker MQTT local (ex. Home Assistant).",
    help_aio:"Publiez les mesures sur des feeds Adafruit IO.",
    help_dim:"Reduit automatiquement la luminosite aux heures configurees. NTP requis.",
    help_ota:"Telechargez et installez un nouveau firmware. L'appareil redemarrera.",
    lbl_regen:"Regenerer les identifiants AP",lbl_sta_connect:"Connecter au prochain redemarrage",
    lbl_mode_text:"Texte",lbl_mode_big:"Grand CO\u2082",lbl_mode_graph:"Graphique",
    lbl_scale_fixed:"400\u20132000 ppm (serre)",lbl_scale_wide:"400\u20133000 ppm (large)",lbl_scale_auto:"Automatique",
    lbl_max_pts:"Tampon d'historique (echantillons)",lbl_cloud_secret:"Secret/token de l'appareil",
    lbl_mqtt_port:"Port",lbl_mqtt_user:"Utilisateur (optionnel)",lbl_mqtt_pass:"Mot de passe (optionnel)",
    lbl_mqtt_prefix:"Prefixe du sujet",lbl_mqtt_interval:"Intervalle de publication (secondes)",
    lbl_aio_group:"Cle du groupe de feeds",lbl_aio_interval:"Intervalle de publication (secondes)",
    nav_calib:"Calibration",nav_back:"Retour aux parametres",nav_ota:"Mise a jour firmware",
    ph_device_id:"co2-noeud-1",ph_sta_ssid:"Nom de votre reseau Wi-Fi",ph_sta_pass:"Mot de passe Wi-Fi",
    ph_cloud_url:"https://api.knowco2.com/v1/ingest",ph_cloud_token:"Collez votre token ici",
    ph_mqtt_broker:"192.168.1.x ou mqtt.exemple.com",ph_mqtt_prefix:"knowco2",
    ph_aio_user:"votre-utilisateur-adafruit",ph_aio_group:"knowco2",
    ph_fw_url:"http://192.168.1.x/firmware.py",
    aria_alerts_check:"Activer ou desactiver les alertes CO\u2082",
    aria_cloud_check:"Activer ou desactiver l'envoi de donnees vers le cloud",
    aria_mqtt_check:"Activer ou desactiver la publication MQTT",
    aria_aio_check:"Activer ou desactiver la publication Adafruit IO",
    aria_dim_check:"Activer ou desactiver l'attenuation programmee",
    lbl_flip:"Flip display (upside-down mount)",
    help_flip:"Rotates the screen 180\u00b0 so it reads correctly when mounted upside down. Buttons are not affected.",
    aria_flip_check:"Toggle display orientation flip",
    skip_nav:"Aller au contenu principal"
  },
  de: {
    title:"Know CO\u2082 Einstellungen",save:"Einstellungen speichern",
    sec_display:"Anzeige & Schwellenwerte",sec_wifi:"Wi-Fi",
    sec_cloud:"Cloud-Upload",sec_mqtt:"MQTT-Broker",
    sec_aio:"Adafruit IO",sec_dim:"Bildschirmabdunkelung",
    sec_device:"Gerat",sec_calib:"Kalibrierung",
    lbl_low:"Niedriger Schwellenwert (ppm)",lbl_med:"Mittlerer Schwellenwert (ppm)",
    lbl_alert:"Alarmschwelle (ppm)",lbl_temp:"Temperatureinheit",
    lbl_alerts:"Alarme aktivieren",lbl_scale:"Diagrammskalierung",
    lbl_ap_ssid:"AP-Netzwerkname",lbl_ap_pass:"AP-Passwort",
    lbl_sta_ssid:"WLAN-SSID zuhause",lbl_sta_pass:"WLAN-Passwort",
    lbl_cloud_url:"API-URL",lbl_cloud_token:"Gerate-Token",
    lbl_cloud_interval:"Upload-Intervall (Sekunden)",lbl_cloud_en:"Cloud-Upload aktivieren",
    lbl_mqtt_en:"MQTT-Veroffentlichung aktivieren",lbl_mqtt_broker:"Broker-Host/IP",
    lbl_aio_en:"Adafruit IO aktivieren",lbl_aio_user:"AIO-Benutzername",
    lbl_dim_en:"Geplante Abdunkelung aktivieren (NTP erforderlich)",
    lbl_dim_start:"Startzeit (0-23)",lbl_dim_end:"Endzeit (0-23)",
    lbl_dim_bright:"Helligkeit wahrend Abdunkelung (0-100%)",
    lbl_lang:"Oberflachensprache",lbl_admin_pw:"Einstellungspasswort",
    lbl_device_id:"Gerate-ID",btn_ota:"Firmware-Update (OTA)",
    note_dim:"Beispiel: Start=22, Ende=7 dunkelt von 22 bis 7 Uhr ab.",
    note_https:"Hinweis: Webserver nutzt HTTP. Datenverkehr bleibt im lokalen Netz.",
    help_low:"CO\u2082-Wert darunter wird grun angezeigt.",
    help_med:"CO\u2082-Wert darunter wird gelb angezeigt.",
    help_alert:"CO\u2082-Wert gleich oder daruber lost Alarm aus.",
    help_device_id:"Kennung, die mit Cloud- und MQTT-Daten gesendet wird.",
    help_admin_pw:"Leer lassen, um Passwortschutz zu deaktivieren.",
    help_sta:"WLAN-Zugangsdaten eingeben, um das Gerat zu verbinden.",
    help_cloud:"CO\u2082-Messwerte an das Know CO\u2082-Dashboard senden.",
    help_mqtt:"Messwerte an einen lokalen MQTT-Broker senden (z.B. Home Assistant).",
    help_aio:"Messwerte an Adafruit IO-Feeds senden.",
    help_dim:"Displayhelligkeit automatisch reduzieren. NTP erforderlich.",
    help_ota:"Neue Firmware herunterladen und installieren. Gerat startet neu.",
    lbl_regen:"AP-Zugangsdaten neu generieren",lbl_sta_connect:"Beim nachsten Neustart verbinden",
    lbl_mode_text:"Text",lbl_mode_big:"Grosses CO\u2082",lbl_mode_graph:"Diagramm",
    lbl_scale_fixed:"400\u20132000 ppm (eng)",lbl_scale_wide:"400\u20133000 ppm (weit)",lbl_scale_auto:"Automatisch",
    lbl_max_pts:"Verlaufspuffer (Messwerte)",lbl_cloud_secret:"Gerate-Geheimnis/Token",
    lbl_mqtt_port:"Port",lbl_mqtt_user:"Benutzer (optional)",lbl_mqtt_pass:"Passwort (optional)",
    lbl_mqtt_prefix:"Topic-Prafix",lbl_mqtt_interval:"Veroffentlichungsintervall (Sekunden)",
    lbl_aio_group:"Feed-Gruppenschlussel",lbl_aio_interval:"Veroffentlichungsintervall (Sekunden)",
    nav_calib:"Kalibrierung",nav_back:"Zuruck zu Einstellungen",nav_ota:"Firmware-Update",
    ph_device_id:"co2-knoten-1",ph_sta_ssid:"Ihr WLAN-Netzwerkname",ph_sta_pass:"Ihr WLAN-Passwort",
    ph_cloud_url:"https://api.knowco2.com/v1/ingest",ph_cloud_token:"Token hier einfugen",
    ph_mqtt_broker:"192.168.1.x oder mqtt.beispiel.de",ph_mqtt_prefix:"knowco2",
    ph_aio_user:"ihr-adafruit-benutzername",ph_aio_group:"knowco2",
    ph_fw_url:"http://192.168.1.x/firmware.py",
    aria_alerts_check:"CO\u2082-Benachrichtigungen ein- oder ausschalten",
    aria_cloud_check:"Cloud-Datenuploads ein- oder ausschalten",
    aria_mqtt_check:"MQTT-Veroffentlichung ein- oder ausschalten",
    aria_aio_check:"Adafruit IO Veroffentlichung ein- oder ausschalten",
    aria_dim_check:"Geplante Abdunkelung ein- oder ausschalten",
    lbl_flip:"Flip display (upside-down mount)",
    help_flip:"Rotates the screen 180\u00b0 so it reads correctly when mounted upside down. Buttons are not affected.",
    aria_flip_check:"Toggle display orientation flip",
    skip_nav:"Zum Hauptinhalt springen"
  },
  pt: {
    title:"Configuracoes Know CO\u2082",save:"Salvar configuracoes",
    sec_display:"Tela e limiares",sec_wifi:"Wi-Fi",
    sec_cloud:"Envio para nuvem",sec_mqtt:"Broker MQTT",
    sec_aio:"Adafruit IO",sec_dim:"Reducao do brilho",
    sec_device:"Dispositivo",sec_calib:"Calibracao",
    lbl_low:"Limiar baixo (ppm)",lbl_med:"Limiar medio (ppm)",
    lbl_alert:"Limiar de alerta (ppm)",lbl_temp:"Unidade de temperatura",
    lbl_alerts:"Ativar alertas",lbl_scale:"Escala do grafico",
    lbl_ap_ssid:"Nome da rede AP",lbl_ap_pass:"Senha AP",
    lbl_sta_ssid:"SSID Wi-Fi casa",lbl_sta_pass:"Senha Wi-Fi",
    lbl_cloud_url:"URL da API",lbl_cloud_token:"Token do dispositivo",
    lbl_cloud_interval:"Intervalo de envio (segundos)",lbl_cloud_en:"Ativar envio para nuvem",
    lbl_mqtt_en:"Ativar publicacao MQTT",lbl_mqtt_broker:"Host/IP do broker",
    lbl_aio_en:"Ativar Adafruit IO",lbl_aio_user:"Usuario AIO",
    lbl_dim_en:"Ativar reducao de brilho programada (requer NTP)",
    lbl_dim_start:"Hora de inicio (0-23)",lbl_dim_end:"Hora de fim (0-23)",
    lbl_dim_bright:"Brilho durante reducao (0-100%)",
    lbl_lang:"Idioma da interface",lbl_admin_pw:"Senha das configuracoes",
    lbl_device_id:"ID do dispositivo",btn_ota:"Atualizacao OTA",
    note_dim:"Exemplo: inicio=22, fim=7 reduz das 22h as 7h.",
    note_https:"Nota: Servidor web usa HTTP. Trafego fica na rede local.",
    help_low:"Nivel de CO\u2082 abaixo deste e exibido em verde.",
    help_med:"Nivel de CO\u2082 abaixo deste e exibido em amarelo.",
    help_alert:"Nivel de CO\u2082 igual ou acima deste dispara um alerta.",
    help_device_id:"Identificador enviado com dados de nuvem e MQTT.",
    help_admin_pw:"Deixe em branco para desativar a protecao por senha.",
    help_sta:"Insira as credenciais Wi-Fi para conectar o dispositivo.",
    help_cloud:"Envie leituras de CO\u2082 para o painel Know CO\u2082.",
    help_mqtt:"Publique leituras em um broker MQTT local (ex. Home Assistant).",
    help_aio:"Publique leituras em feeds do Adafruit IO.",
    help_dim:"Reduce automaticamente o brilho nos horarios configurados. Requer NTP.",
    help_ota:"Baixe e instale novo firmware. O dispositivo vai reiniciar.",
    lbl_regen:"Regenerar credenciais AP",lbl_sta_connect:"Conectar no proximo reinicio",
    lbl_mode_text:"Texto",lbl_mode_big:"CO\u2082 grande",lbl_mode_graph:"Grafico",
    lbl_scale_fixed:"400\u20132000 ppm (estreito)",lbl_scale_wide:"400\u20133000 ppm (largo)",lbl_scale_auto:"Automatico",
    lbl_max_pts:"Buffer de historico (amostras)",lbl_cloud_secret:"Segredo/token do dispositivo",
    lbl_mqtt_port:"Porta",lbl_mqtt_user:"Usuario (opcional)",lbl_mqtt_pass:"Senha (opcional)",
    lbl_mqtt_prefix:"Prefixo do topico",lbl_mqtt_interval:"Intervalo de publicacao (segundos)",
    lbl_aio_group:"Chave do grupo de feeds",lbl_aio_interval:"Intervalo de publicacao (segundos)",
    nav_calib:"Calibracao",nav_back:"Voltar as Configuracoes",nav_ota:"Atualizar firmware",
    ph_device_id:"co2-node-1",ph_sta_ssid:"Nome da sua rede Wi-Fi",ph_sta_pass:"Senha Wi-Fi",
    ph_cloud_url:"https://api.knowco2.com/v1/ingest",ph_cloud_token:"Cole seu token aqui",
    ph_mqtt_broker:"192.168.1.x ou mqtt.exemplo.com",ph_mqtt_prefix:"knowco2",
    ph_aio_user:"seu-usuario-adafruit",ph_aio_group:"knowco2",
    ph_fw_url:"http://192.168.1.x/firmware.py",
    aria_alerts_check:"Ativar ou desativar alertas de CO\u2082",
    aria_cloud_check:"Ativar ou desativar upload de dados para a nuvem",
    aria_mqtt_check:"Ativar ou desativar publicacao MQTT",
    aria_aio_check:"Ativar ou desativar publicacao no Adafruit IO",
    aria_dim_check:"Ativar ou desativar reducao de brilho programada",
    lbl_flip:"Flip display (upside-down mount)",
    help_flip:"Rotates the screen 180\u00b0 so it reads correctly when mounted upside down. Buttons are not affected.",
    aria_flip_check:"Toggle display orientation flip",
    skip_nav:"Ir para o conteudo principal"
  },
  it: {
    title:"Impostazioni Know CO\u2082",save:"Salva impostazioni",
    sec_display:"Display e soglie",sec_wifi:"Wi-Fi",
    sec_cloud:"Caricamento cloud",sec_mqtt:"Broker MQTT",
    sec_aio:"Adafruit IO",sec_dim:"Attenuazione display",
    sec_device:"Dispositivo",sec_calib:"Calibrazione",
    lbl_low:"Soglia bassa (ppm)",lbl_med:"Soglia media (ppm)",
    lbl_alert:"Soglia di allarme (ppm)",lbl_temp:"Unita di temperatura",
    lbl_alerts:"Abilita allarmi",lbl_scale:"Scala del grafico",
    lbl_ap_ssid:"Nome rete AP",lbl_ap_pass:"Password AP",
    lbl_sta_ssid:"SSID Wi-Fi casa",lbl_sta_pass:"Password Wi-Fi",
    lbl_cloud_url:"URL API",lbl_cloud_token:"Token dispositivo",
    lbl_cloud_interval:"Intervallo upload (secondi)",lbl_cloud_en:"Abilita upload cloud",
    lbl_mqtt_en:"Abilita pubblicazione MQTT",lbl_mqtt_broker:"Host/IP broker",
    lbl_aio_en:"Abilita Adafruit IO",lbl_aio_user:"Utente AIO",
    lbl_dim_en:"Abilita attenuazione programmata (richiede NTP)",
    lbl_dim_start:"Ora inizio (0-23)",lbl_dim_end:"Ora fine (0-23)",
    lbl_dim_bright:"Luminosita durante attenuazione (0-100%)",
    lbl_lang:"Lingua interfaccia",lbl_admin_pw:"Password impostazioni",
    lbl_device_id:"ID dispositivo",btn_ota:"Aggiornamento OTA",
    note_dim:"Esempio: inizio=22, fine=7 attenua dalle 22 alle 7.",
    note_https:"Nota: Il server web usa HTTP. Il traffico resta sulla rete locale.",
    help_low:"Il livello di CO\u2082 sotto questo viene mostrato in verde.",
    help_med:"Il livello di CO\u2082 sotto questo viene mostrato in giallo.",
    help_alert:"Il livello di CO\u2082 uguale o superiore attiva un allarme.",
    help_device_id:"Identificatore inviato con i dati cloud e MQTT.",
    help_admin_pw:"Lasciare vuoto per disabilitare la protezione con password.",
    help_sta:"Inserisci le credenziali Wi-Fi per connettere il dispositivo.",
    help_cloud:"Invia le letture di CO\u2082 al pannello Know CO\u2082.",
    help_mqtt:"Pubblica le letture su un broker MQTT locale (es. Home Assistant).",
    help_aio:"Pubblica le letture su feed di Adafruit IO.",
    help_dim:"Riduce automaticamente la luminosita nelle ore configurate. Richiede NTP.",
    help_ota:"Scarica e installa nuovo firmware. Il dispositivo si riavviera.",
    lbl_regen:"Rigenera credenziali AP",lbl_sta_connect:"Connetti al prossimo riavvio",
    lbl_mode_text:"Testo",lbl_mode_big:"CO\u2082 grande",lbl_mode_graph:"Grafico",
    lbl_scale_fixed:"400\u20132000 ppm (stretto)",lbl_scale_wide:"400\u20133000 ppm (ampio)",lbl_scale_auto:"Automatico",
    lbl_max_pts:"Buffer storico (campioni)",lbl_cloud_secret:"Segreto/token del dispositivo",
    lbl_mqtt_port:"Porta",lbl_mqtt_user:"Utente (opzionale)",lbl_mqtt_pass:"Password (opzionale)",
    lbl_mqtt_prefix:"Prefisso topic",lbl_mqtt_interval:"Intervallo di pubblicazione (secondi)",
    lbl_aio_group:"Chiave gruppo feed",lbl_aio_interval:"Intervallo di pubblicazione (secondi)",
    nav_calib:"Calibrazione",nav_back:"Torna alle Impostazioni",nav_ota:"Aggiornamento firmware",
    ph_device_id:"co2-nodo-1",ph_sta_ssid:"Nome della tua rete Wi-Fi",ph_sta_pass:"Password Wi-Fi",
    ph_cloud_url:"https://api.knowco2.com/v1/ingest",ph_cloud_token:"Incolla il tuo token qui",
    ph_mqtt_broker:"192.168.1.x o mqtt.esempio.com",ph_mqtt_prefix:"knowco2",
    ph_aio_user:"tuo-utente-adafruit",ph_aio_group:"knowco2",
    ph_fw_url:"http://192.168.1.x/firmware.py",
    aria_alerts_check:"Attiva o disattiva gli avvisi CO\u2082",
    aria_cloud_check:"Attiva o disattiva il caricamento cloud",
    aria_mqtt_check:"Attiva o disattiva la pubblicazione MQTT",
    aria_aio_check:"Attiva o disattiva la pubblicazione Adafruit IO",
    aria_dim_check:"Attiva o disattiva l'attenuazione programmata",
    lbl_flip:"Flip display (upside-down mount)",
    help_flip:"Rotates the screen 180\u00b0 so it reads correctly when mounted upside down. Buttons are not affected.",
    aria_flip_check:"Toggle display orientation flip",
    skip_nav:"Vai al contenuto principale"
  },
  ja: {
    title:"Know CO\u2082 \u8a2d\u5b9a",save:"\u8a2d\u5b9a\u3092\u4fdd\u5b58",
    sec_display:"\u8868\u793a\u3068\u3057\u304d\u3044\u5024",sec_wifi:"Wi-Fi",
    sec_cloud:"\u30af\u30e9\u30a6\u30c9\u30a2\u30c3\u30d7\u30ed\u30fc\u30c9",sec_mqtt:"MQTT\u30d6\u30ed\u30fc\u30ab\u30fc",
    sec_aio:"Adafruit IO",sec_dim:"\u753b\u9762\u8abf\u5149",
    sec_device:"\u30c7\u30d0\u30a4\u30b9",sec_calib:"\u30ad\u30e3\u30ea\u30d6\u30ec\u30fc\u30b7\u30e7\u30f3",
    lbl_low:"\u4f4e\u3057\u304d\u3044\u5024 (ppm)",lbl_med:"\u4e2d\u3057\u304d\u3044\u5024 (ppm)",
    lbl_alert:"\u8b66\u544a\u3057\u304d\u3044\u5024 (ppm)",lbl_temp:"\u6e29\u5ea6\u5358\u4f4d",
    lbl_alerts:"\u30a2\u30e9\u30fc\u30c8\u3092\u6709\u52b9\u306b\u3059\u308b",lbl_scale:"\u30b0\u30e9\u30d5\u30b9\u30b1\u30fc\u30eb",
    lbl_ap_ssid:"AP\u30cd\u30c3\u30c8\u30ef\u30fc\u30af\u540d",lbl_ap_pass:"AP\u30d1\u30b9\u30ef\u30fc\u30c9",
    lbl_sta_ssid:"\u30db\u30fcWi-Fi SSID",lbl_sta_pass:"Wi-Fi\u30d1\u30b9\u30ef\u30fc\u30c9",
    lbl_cloud_url:"API URL",lbl_cloud_token:"\u30c7\u30d0\u30a4\u30b9\u30c8\u30fc\u30af\u30f3",
    lbl_cloud_interval:"\u9001\u4fe1\u9593\u9694\uff08\u79d2\uff09",lbl_cloud_en:"\u30af\u30e9\u30a6\u30c9\u30a2\u30c3\u30d7\u30ed\u30fc\u30c9\u3092\u6709\u52b9\u306b\u3059\u308b",
    lbl_mqtt_en:"MQTT\u914d\u4fe1\u3092\u6709\u52b9\u306b\u3059\u308b",lbl_mqtt_broker:"\u30d6\u30ed\u30fc\u30ab\u30fc\u30db\u30b9\u30c8/IP",
    lbl_aio_en:"Adafruit IO\u3092\u6709\u52b9\u306b\u3059\u308b",lbl_aio_user:"AIO\u30e6\u30fc\u30b6\u30fc\u540d",
    lbl_dim_en:"\u30b9\u30b1\u30b8\u30e5\u30fc\u30eb\u8abf\u5149\u3092\u6709\u52b9\u306b\u3059\u308b\uff08NTP\u5fc5\u8981\uff09",
    lbl_dim_start:"\u958b\u59cb\u6642\u523b (0-23)",lbl_dim_end:"\u7d42\u4e86\u6642\u523b (0-23)",
    lbl_dim_bright:"\u8abf\u5149\u4e2d\u306e\u8f1d\u5ea6 (0-100%)",
    lbl_lang:"\u30a4\u30f3\u30bf\u30fc\u30d5\u30a7\u30fc\u30b9\u8a00\u8a9e",lbl_admin_pw:"\u8a2d\u5b9a\u30d1\u30b9\u30ef\u30fc\u30c9",
    lbl_device_id:"\u30c7\u30d0\u30a4\u30b9ID",btn_ota:"OTA\u30d5\u30a1\u30fc\u30e0\u30a6\u30a7\u30a2\u66f4\u65b0",
    note_dim:"\u4f8b: \u958b\u59cb=22, \u7d42\u4e86=7\u306f22\u664200\u5206\u304b\u307a7\u664200\u5206\u307e\u3067\u8abf\u5149\u3002",
    note_https:"\u6ce8\u610f: \u30ed\u30fc\u30ab\u30eb\u30b5\u30fc\u30d0\u30fc\u306fHTTP\u3092\u4f7f\u7528\u3002\u901a\u4fe1\u306fLAN\u5185\u306b\u3068\u3069\u307e\u308a\u307e\u3059\u3002",
    help_low:"CO\u2082\u30ec\u30d9\u30eb\u304c\u3053\u308c\u4ee5\u4e0b\u306e\u5834\u5408\u7dd1\u8272\u3067\u8868\u793a\u3002",
    help_med:"CO\u2082\u30ec\u30d9\u30eb\u304c\u3053\u308c\u4ee5\u4e0b\u306e\u5834\u5408\u9ec4\u8272\u3067\u8868\u793a\u3002",
    help_alert:"\u3053\u306e\u5024\u4ee5\u4e0a\u306eCO\u2082\u30ec\u30d9\u30eb\u3067\u30a2\u30e9\u30fc\u30c8\u3002",
    help_device_id:"\u30af\u30e9\u30a6\u30c9\u304a\u3088\u3073MQTT\u30c7\u30fc\u30bf\u306b\u9644\u52a0\u3055\u308c\u308b\u8b58\u5225\u5b50\u3002",
    help_admin_pw:"\u30d1\u30b9\u30ef\u30fc\u30c9\u3092\u7121\u52b9\u306b\u3059\u308b\u306b\u306f\u7a7a\u767d\u306b\u3057\u3066\u304f\u3060\u3055\u3044\u3002",
    help_sta:"Wi-Fi\u8cc7\u683c\u60c5\u5831\u3092\u5165\u529b\u3057\u3066\u30c7\u30d0\u30a4\u30b9\u3092\u63a5\u7d9a\u3002",
    help_cloud:"CO\u2082\u8a08\u6e2c\u5024\u3092Know CO\u2082\u30c0\u30c3\u30b7\u30e5\u30dc\u30fc\u30c9\u306b\u9001\u4fe1\u3002",
    help_mqtt:"\u30ed\u30fc\u30ab\u30ebMQTT\u30d6\u30ed\u30fc\u30ab\u30fc\u306b\u8a08\u6e2c\u5024\u3092\u9001\u4fe1\u3002",
    help_aio:"Adafruit IO\u30d5\u30a3\u30fc\u30c9\u306b\u8a08\u6e2c\u5024\u3092\u9001\u4fe1\u3002",
    help_dim:"\u8a2d\u5b9a\u6642\u9593\u306b\u81ea\u52d5\u3067\u753b\u9762\u3092\u6697\u304f\u3059\u308b\u3002NTP\u5fc5\u8981\u3002",
    help_ota:"\u65b0\u3057\u3044\u30d5\u30a1\u30fc\u30e0\u30a6\u30a7\u30a2\u3092\u30c0\u30a6\u30f3\u30ed\u30fc\u30c9\u3057\u30a4\u30f3\u30b9\u30c8\u30fc\u30eb\u3002",
    lbl_regen:"AP\u8cc7\u683c\u60c5\u5831\u3092\u518d\u751f\u6210",lbl_sta_connect:"\u6b21\u306e\u518d\u8d77\u52d5\u6642\u306b\u63a5\u7d9a",
    lbl_mode_text:"\u30c6\u30ad\u30b9\u30c8",lbl_mode_big:"\u5927\u304dCO\u2082",lbl_mode_graph:"\u30b0\u30e9\u30d5",
    lbl_scale_fixed:"400\u20132000 ppm (\u9577\u65b9\u5f62)",lbl_scale_wide:"400\u20133000 ppm (\u5e83\u3044)",lbl_scale_auto:"\u81ea\u52d5",
    lbl_max_pts:"\u5c65\u6b74\u30d0\u30c3\u30d5\u30a1\uff08\u30b5\u30f3\u30d7\u30eb\uff09",lbl_cloud_secret:"\u30c7\u30d0\u30a4\u30b9\u30b7\u30fc\u30af\u30ec\u30c3\u30c8/\u30c8\u30fc\u30af\u30f3",
    lbl_mqtt_port:"\u30dd\u30fc\u30c8",lbl_mqtt_user:"\u30e6\u30fc\u30b6\u30fc\uff08\u4efb\u610f\uff09",lbl_mqtt_pass:"\u30d1\u30b9\u30ef\u30fc\u30c9\uff08\u4efb\u610f\uff09",
    lbl_mqtt_prefix:"\u30c8\u30d4\u30c3\u30af\u30d7\u30ec\u30d5\u30a3\u30c3\u30af\u30b9",lbl_mqtt_interval:"\u9001\u4fe1\u9593\u9694\uff08\u79d2\uff09",
    lbl_aio_group:"\u30d5\u30a3\u30fc\u30c9\u30b0\u30eb\u30fc\u30d7\u30ad\u30fc",lbl_aio_interval:"\u9001\u4fe1\u9593\u9694\uff08\u79d2\uff09",
    nav_calib:"\u30ad\u30e3\u30ea\u30d6\u30ec\u30fc\u30b7\u30e7\u30f3",nav_back:"\u8a2d\u5b9a\u306b\u623b\u308b",nav_ota:"\u30d5\u30a1\u30fc\u30e0\u30a6\u30a7\u30a2\u66f4\u65b0",
    ph_device_id:"co2-node-1",ph_sta_ssid:"Wi-Fi\u30cd\u30c3\u30c8\u30ef\u30fc\u30af\u540d",ph_sta_pass:"Wi-Fi\u30d1\u30b9\u30ef\u30fc\u30c9",
    ph_cloud_url:"https://api.knowco2.com/v1/ingest",ph_cloud_token:"\u30c8\u30fc\u30af\u30f3\u3092\u8cbc\u308a\u4ed8\u3051",
    ph_mqtt_broker:"192.168.1.x \u307e\u305f\u306f mqtt.\u4f8b\u3002com",ph_mqtt_prefix:"knowco2",
    ph_aio_user:"adafruit\u30e6\u30fc\u30b6\u30fc\u540d",ph_aio_group:"knowco2",
    ph_fw_url:"http://192.168.1.x/firmware.py",
    aria_alerts_check:"CO\u2082\u8b66\u544a\u901a\u77e5\u306e\u5207\u308a\u66ff\u3048",
    aria_cloud_check:"\u30af\u30e9\u30a6\u30c9\u30c7\u30fc\u30bf\u9001\u4fe1\u306e\u5207\u308a\u66ff\u3048",
    aria_mqtt_check:"MQTT\u914d\u4fe1\u306e\u5207\u308a\u66ff\u3048",
    aria_aio_check:"Adafruit IO\u914d\u4fe1\u306e\u5207\u308a\u66ff\u3048",
    aria_dim_check:"\u30b9\u30b1\u30b8\u30e5\u30fc\u30eb\u8abf\u5149\u306e\u5207\u308a\u66ff\u3048",
    lbl_flip:"Flip display (upside-down mount)",
    help_flip:"Rotates the screen 180\u00b0 so it reads correctly when mounted upside down. Buttons are not affected.",
    aria_flip_check:"Toggle display orientation flip",
    skip_nav:"\u30e1\u30a4\u30f3\u30b3\u30f3\u30c6\u30f3\u30c4\u306b\u30b9\u30ad\u30c3\u30d7"
  },
  zh: {
    title:"Know CO\u2082 \u8bbe\u7f6e",save:"\u4fdd\u5b58\u8bbe\u7f6e",
    sec_display:"\u663e\u793a\u4e0e\u9608\u503c",sec_wifi:"Wi-Fi",
    sec_cloud:"\u4e91\u4e0a\u4f20",sec_mqtt:"MQTT\u4e2d\u7ee7\u5668",
    sec_aio:"Adafruit IO",sec_dim:"\u5c4f\u5e55\u8c03\u5149",
    sec_device:"\u8bbe\u5907",sec_calib:"\u6821\u51c6",
    lbl_low:"\u4f4e\u9608\u503c (ppm)",lbl_med:"\u4e2d\u9608\u503c (ppm)",
    lbl_alert:"\u8b66\u62a5\u9608\u503c (ppm)",lbl_temp:"\u6e29\u5ea6\u5355\u4f4d",
    lbl_alerts:"\u542f\u7528\u8b66\u62a5",lbl_scale:"\u56fe\u8868\u5c3a\u5ea6",
    lbl_ap_ssid:"AP\u7f51\u7edc\u540d\u79f0",lbl_ap_pass:"AP\u5bc6\u7801",
    lbl_sta_ssid:"\u5bb6\u5ead Wi-Fi SSID",lbl_sta_pass:"Wi-Fi\u5bc6\u7801",
    lbl_cloud_url:"API \u5730\u5740",lbl_cloud_token:"\u8bbe\u5907\u4ee4\u724c",
    lbl_cloud_interval:"\u4e0a\u4f20\u95f4\u9694\uff08\u79d2\uff09",lbl_cloud_en:"\u542f\u7528\u4e91\u4e0a\u4f20",
    lbl_mqtt_en:"\u542f\u7528 MQTT \u53d1\u5e03",lbl_mqtt_broker:"\u4e2d\u7ee7\u5668\u4e3b\u673a/IP",
    lbl_aio_en:"\u542f\u7528 Adafruit IO",lbl_aio_user:"AIO \u7528\u6237\u540d",
    lbl_dim_en:"\u542f\u7528\u5b9a\u65f6\u8c03\u5149\uff08\u9700\u8981 NTP\uff09",
    lbl_dim_start:"\u5f00\u59cb\u65f6\u95f4 (0-23)",lbl_dim_end:"\u7ed3\u675f\u65f6\u95f4 (0-23)",
    lbl_dim_bright:"\u8c03\u5149\u671f\u95f4\u4eae\u5ea6 (0-100%)",
    lbl_lang:"\u754c\u9762\u8bed\u8a00",lbl_admin_pw:"\u8bbe\u7f6e\u5bc6\u7801",
    lbl_device_id:"\u8bbe\u5907 ID",btn_ota:"OTA \u56fa\u4ef6\u66f4\u65b0",
    note_dim:"\u793a\u4f8b\uff1a\u5f00\u59cb=22, \u7ed3\u675f=7 \u5c06\u4ece22\u70b9\u8c03\u5149\u521307\u70b9\u3002",
    note_https:"\u6ce8\u610f\uff1a\u672c\u5730\u670d\u52a1\u5668\u4f7f\u7528 HTTP\u3002\u6d41\u91cf\u4ec5\u5728\u5c40\u57df\u7f51\u5185\u3002",
    help_low:"CO\u2082\u6c34\u5e73\u4f4e\u4e8e\u6b64\u503c\u65f6\u663e\u793a\u7eff\u8272\u3002",
    help_med:"CO\u2082\u6c34\u5e73\u4f4e\u4e8e\u6b64\u503c\u65f6\u663e\u793a\u9ec4\u8272\u3002",
    help_alert:"CO\u2082\u6c34\u5e73\u8fbe\u5230\u6216\u8d85\u8fc7\u6b64\u503c\u65f6\u89e6\u53d1\u8b66\u62a5\u3002",
    help_device_id:"\u968f\u4e91\u548c MQTT \u6570\u636e\u4e00\u8d77\u53d1\u9001\u7684\u6807\u8bc6\u7b26\u3002",
    help_admin_pw:"\u7559\u7a7a\u53ef\u7981\u7528\u5bc6\u7801\u4fdd\u62a4\u3002",
    help_sta:"\u8f93\u5165 Wi-Fi \u51ed\u636e\u4ee5\u8fde\u63a5\u8bbe\u5907\u3002",
    help_cloud:"\u5c06 CO\u2082 \u8bfb\u6570\u53d1\u9001\u5230 Know CO\u2082 \u4e91\u5e73\u53f0\u3002",
    help_mqtt:"\u5c06\u8bfb\u6570\u53d1\u5e03\u5230\u672c\u5730 MQTT \u4e2d\u7ee7\u5668\uff08\u5982 Home Assistant\uff09\u3002",
    help_aio:"\u5c06\u8bfb\u6570\u53d1\u5e03\u5230 Adafruit IO \u9879\u76ee\u3002",
    help_dim:"\u5728\u8bbe\u5b9a\u65f6\u95f4\u81ea\u52a8\u964d\u4f4e\u4eae\u5ea6\u3002\u9700\u8981 NTP\u3002",
    help_ota:"\u4e0b\u8f7d\u5e76\u5b89\u88c5\u65b0\u56fa\u4ef6\u3002\u8bbe\u5907\u5c06\u91cd\u542f\u3002",
    lbl_regen:"\u91cd\u65b0\u751f\u6210 AP \u51ed\u636e",lbl_sta_connect:"\u4e0b\u6b21\u91cd\u542f\u65f6\u8fde\u63a5",
    lbl_mode_text:"\u6587\u5b57",lbl_mode_big:"\u5927\u5b57 CO\u2082",lbl_mode_graph:"\u56fe\u8868",
    lbl_scale_fixed:"400\u20132000 ppm\uff08\u7d27\u51d1\uff09",lbl_scale_wide:"400\u20133000 ppm\uff08\u5bbd\u677e\uff09",lbl_scale_auto:"\u81ea\u52a8",
    lbl_max_pts:"\u5386\u53f2\u7f13\u51b2\u533a\uff08\u6837\u672c\uff09",lbl_cloud_secret:"\u8bbe\u5907\u5bc6\u94a5/\u4ee4\u724c",
    lbl_mqtt_port:"\u7aef\u53e3",lbl_mqtt_user:"\u7528\u6237\u540d\uff08\u53ef\u9009\uff09",lbl_mqtt_pass:"\u5bc6\u7801\uff08\u53ef\u9009\uff09",
    lbl_mqtt_prefix:"\u4e3b\u9898\u524d\u7f00",lbl_mqtt_interval:"\u53d1\u5e03\u95f4\u9694\uff08\u79d2\uff09",
    lbl_aio_group:"\u9879\u76ee\u7ec4\u5bc6\u94a5",lbl_aio_interval:"\u53d1\u5e03\u95f4\u9694\uff08\u79d2\uff09",
    nav_calib:"\u6821\u51c6",nav_back:"\u8fd4\u56de\u8bbe\u7f6e",nav_ota:"\u56fa\u4ef6\u66f4\u65b0",
    ph_device_id:"co2-node-1",ph_sta_ssid:"Wi-Fi \u7f51\u7edc\u540d\u79f0",ph_sta_pass:"Wi-Fi \u5bc6\u7801",
    ph_cloud_url:"https://api.knowco2.com/v1/ingest",ph_cloud_token:"\u5c06\u4ee4\u724c\u7c98\u8d34\u5230\u8fd9\u91cc",
    ph_mqtt_broker:"192.168.1.x \u6216 mqtt.\u793a\u4f8b.com",ph_mqtt_prefix:"knowco2",
    ph_aio_user:"adafruit\u7528\u6237\u540d",ph_aio_group:"knowco2",
    ph_fw_url:"http://192.168.1.x/firmware.py",
    aria_alerts_check:"\u5207\u6362 CO\u2082 \u8b66\u62a5\u901a\u77e5",
    aria_cloud_check:"\u5207\u6362\u4e91\u6570\u636e\u4e0a\u4f20",
    aria_mqtt_check:"\u5207\u6362 MQTT \u53d1\u5e03",
    aria_aio_check:"\u5207\u6362 Adafruit IO \u53d1\u5e03",
    aria_dim_check:"\u5207\u6362\u5b9a\u65f6\u8c03\u5149",
    lbl_flip:"Flip display (upside-down mount)",
    help_flip:"Rotates the screen 180\u00b0 so it reads correctly when mounted upside down. Buttons are not affected.",
    aria_flip_check:"Toggle display orientation flip",
    skip_nav:"\u8df3\u81f3\u4e3b\u8981\u5185\u5bb9"
  },
  ko: {
    title:"Know CO\u2082 \uc124\uc815",save:"\uc124\uc815 \uc800\uc7a5",
    sec_display:"\ub514\uc2a4\ud50c\ub808\uc774 \ubc0f \uc784\uacc4\uac12",sec_wifi:"Wi-Fi",
    sec_cloud:"\ud074\ub77c\uc6b0\ub4dc \uc5c5\ub85c\ub4dc",sec_mqtt:"MQTT \ube0c\ub85c\ucee4",
    sec_aio:"Adafruit IO",sec_dim:"\ud654\uba74 \ubc1d\uae30 \uc608\uc57d",
    sec_device:"\uc7a5\uce58",sec_calib:"\ubcf4\uc815",
    lbl_low:"\ub099\uc740 \uc784\uacc4\uac12 (ppm)",lbl_med:"\uc911\uac04 \uc784\uacc4\uac12 (ppm)",
    lbl_alert:"\uacbd\ubcf4 \uc784\uacc4\uac12 (ppm)",lbl_temp:"\uc628\ub3c4 \ub2e8\uc704",
    lbl_alerts:"\uacbd\ubcf4 \ud65c\uc131\ud654",lbl_scale:"\uadf8\ub798\ud504 \ucca0\ub3c4",
    lbl_ap_ssid:"AP \ub124\ud2b8\uc6cc\ud06c \uc774\ub984",lbl_ap_pass:"AP \ube44\ubc00\ubc88\ud638",
    lbl_sta_ssid:"\ud648 Wi-Fi SSID",lbl_sta_pass:"Wi-Fi \ube44\ubc00\ubc88\ud638",
    lbl_cloud_url:"API URL",lbl_cloud_token:"\uc7a5\uce58 \ud1a0\ud070",
    lbl_cloud_interval:"\uc5c5\ub85c\ub4dc \uac04\uaca9 (\ucd08)",lbl_cloud_en:"\ud074\ub77c\uc6b0\ub4dc \uc5c5\ub85c\ub4dc \ud65c\uc131\ud654",
    lbl_mqtt_en:"MQTT \uac8c\uc2dc \ud65c\uc131\ud654",lbl_mqtt_broker:"\ube0c\ub85c\ucee4 \ud638\uc2a4\ud2b8/IP",
    lbl_aio_en:"Adafruit IO \ud65c\uc131\ud654",lbl_aio_user:"AIO \uc0ac\uc6a9\uc790\uba85",
    lbl_dim_en:"\uc608\uc57d \ubc1d\uae30 \uc870\uc808 \ud65c\uc131\ud654 (NTP \ud544\uc694)",
    lbl_dim_start:"\uc2dc\uc791 \uc2dc\uac04 (0-23)",lbl_dim_end:"\uc885\ub8cc \uc2dc\uac04 (0-23)",
    lbl_dim_bright:"\uc5b4\ub450\uc6b4 \uc2dc\uac04 \ubc1d\uae30 (0-100%)",
    lbl_lang:"\uc778\ud130\ud398\uc774\uc2a4 \uc5b8\uc5b4",lbl_admin_pw:"\uc124\uc815 \ube44\ubc00\ubc88\ud638",
    lbl_device_id:"\uc7a5\uce58 ID",btn_ota:"OTA \ud3fc\uc6e8\uc5b4 \uc5c5\ub370\uc774\ud2b8",
    note_dim:"\uc608: \uc2dc\uc791=22, \uc885\ub8cc=7\uc740 \uc624\ud6c4 10\uc2dc\ubd80\ud130 \uc624\uc804 7\uc2dc\uae4c\uc9c0 \uc870\uc808.",
    note_https:"\ucc38\uace0: \ub85c\ucec8 \uc11c\ubc84\ub294 HTTP\ub97c \uc0ac\uc6a9\ud569\ub2c8\ub2e4. \ud2b8\ub798\ud53d\uc740 LAN \ub0b4\uc5d0\ub9e8 \uc788\uc2b5\ub2c8\ub2e4.",
    help_low:"CO\u2082 \uc218\uc900\uc774 \uc774 \uac12 \uc774\ud558\uc774\uba74 \ub179\uc0c9\uc73c\ub85c \ud45c\uc2dc\ub429\ub2c8\ub2e4.",
    help_med:"CO\u2082 \uc218\uc900\uc774 \uc774 \uac12 \uc774\ud558\uc774\uba74 \ub178\ub780\uc0c9\uc73c\ub85c \ud45c\uc2dc\ub429\ub2c8\ub2e4.",
    help_alert:"\uc774 \uac12 \uc774\uc0c1\uc758 CO\u2082 \uc218\uc900\uc5d0\uc11c \uacbd\ubcf4\uac00 \ud65c\uc131\ud654\ub429\ub2c8\ub2e4.",
    help_device_id:"\ud074\ub77c\uc6b0\ub4dc \ubc0f MQTT \ub370\uc774\ud130\uc640 \ud568\uaed8 \uc804\uc1a1\ub418\ub294 \uc2dd\ubcc4\uc790.",
    help_admin_pw:"\ube44\ubc00\ubc88\ud638 \ubcf4\ud638\ub97c \ube44\ud65c\uc131\ud654\ud558\ub824\uba74 \ube44\uc6cc\ub450\uc138\uc694.",
    help_sta:"Wi-Fi \uc790\uaca9 \uc99d\uba85\uc744 \uc785\ub825\ud558\uc5ec \uc7a5\uce58\ub97c \uc5f0\uacb0\ud558\uc138\uc694.",
    help_cloud:"CO\u2082 \uce21\uc815\uac12\uc744 Know CO\u2082 \ud074\ub77c\uc6b0\ub4dc\ub85c \uc804\uc1a1\ud569\ub2c8\ub2e4.",
    help_mqtt:"\ub85c\uce7c MQTT \ube0c\ub85c\ucee4\uc5d0 \uce21\uc815\uac12\uc744 \uac8c\uc2dc\ud569\ub2c8\ub2e4.",
    help_aio:"Adafruit IO \ud53c\ub4dc\uc5d0 \uce21\uc815\uac12\uc744 \uac8c\uc2dc\ud569\ub2c8\ub2e4.",
    help_dim:"\uc124\uc815\ub41c \uc2dc\uac04\ub300\uc5d0 \uc790\ub3d9\uc73c\ub85c \ud654\uba74 \ubc1d\uae30\ub97c \uc904\uc785\ub2c8\ub2e4. NTP \ud544\uc694.",
    help_ota:"\uc0c8 \ud3fc\uc6e8\uc5b4\ub97c \ub2e4\uc6b4\ub85c\ub4dc\ud558\uace0 \uc124\uce58\ud569\ub2c8\ub2e4. \uc7a5\uce58\uac00 \uc7ac\ubd80\ud305\ub429\ub2c8\ub2e4.",
    lbl_regen:"AP \uc790\uaca9 \uc99d\uba85 \uc7ac\uc0dd\uc131",lbl_sta_connect:"\ub2e4\uc74c \uc7ac\ubd80\ud305 \uc2dc \uc5f0\uacb0",
    lbl_mode_text:"\ud14d\uc2a4\ud2b8",lbl_mode_big:"\ub300\ud615 CO\u2082",lbl_mode_graph:"\uadf8\ub798\ud504",
    lbl_scale_fixed:"400\u20132000 ppm (\uc9e7\uc740)",lbl_scale_wide:"400\u20133000 ppm (\ub113\uc740)",lbl_scale_auto:"\uc790\ub3d9",
    lbl_max_pts:"\ud788\uc2a4\ud1a0\ub9ac \ubc84\ud37c (\uc0d8\ud50c)",lbl_cloud_secret:"\uc7a5\uce58 \ube44\ubc00/\ud1a0\ud070",
    lbl_mqtt_port:"\ud3ec\ud2b8",lbl_mqtt_user:"\uc0ac\uc6a9\uc790 (\uc120\ud0dd)",lbl_mqtt_pass:"\ube44\ubc00\ubc88\ud638 (\uc120\ud0dd)",
    lbl_mqtt_prefix:"\ud1a0\ud53d \uc811\ub450\uc0ac",lbl_mqtt_interval:"\uac8c\uc2dc \uac04\uaca9 (\ucd08)",
    lbl_aio_group:"\ud53c\ub4dc \uadf8\ub8f9 \ud0a4",lbl_aio_interval:"\uac8c\uc2dc \uac04\uaca9 (\ucd08)",
    nav_calib:"\ubcf4\uc815",nav_back:"\uc124\uc815\uc73c\ub85c \ub3cc\uc544\uac00\uae30",nav_ota:"\ud3fc\uc6e8\uc5b4 \uc5c5\ub370\uc774\ud2b8",
    ph_device_id:"co2-node-1",ph_sta_ssid:"Wi-Fi \ub124\ud2b8\uc6cc\ud06c \uc774\ub984",ph_sta_pass:"Wi-Fi \ube44\ubc00\ubc88\ud638",
    ph_cloud_url:"https://api.knowco2.com/v1/ingest",ph_cloud_token:"\ud1a0\ud070\uc744 \uc5ec\uae30\uc5d0 \ubd99\uc5ec\ub123\uc73c\uc138\uc694",
    ph_mqtt_broker:"192.168.1.x \ub610\ub294 mqtt.\uc608\uc2dc.com",ph_mqtt_prefix:"knowco2",
    ph_aio_user:"adafruit \uc0ac\uc6a9\uc790\uba85",ph_aio_group:"knowco2",
    ph_fw_url:"http://192.168.1.x/firmware.py",
    aria_alerts_check:"CO\u2082 \uacbd\ubcf4 \uc54c\ub9bc \ud1a0\uae00",
    aria_cloud_check:"\ud074\ub77c\uc6b0\ub4dc \ub370\uc774\ud130 \uc5c5\ub85c\ub4dc \ud1a0\uae00",
    aria_mqtt_check:"MQTT \uac8c\uc2dc \ud1a0\uae00",
    aria_aio_check:"Adafruit IO \uac8c\uc2dc \ud1a0\uae00",
    aria_dim_check:"\uc608\uc57d \ubc1d\uae30 \uc870\uc808 \ud1a0\uae00",
    lbl_flip:"Flip display (upside-down mount)",
    help_flip:"Rotates the screen 180\u00b0 so it reads correctly when mounted upside down. Buttons are not affected.",
    aria_flip_check:"Toggle display orientation flip",
    skip_nav:"\ubcf8\ubb38\uc73c\ub85c \uac74\ub108\ubf00\uae30"
  }
};
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
