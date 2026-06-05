# knowco2/telemetry/mqtt.py
# ----------------------------------------------------------------------
# MQTT publishing: generic broker (e.g. Home Assistant / Mosquitto),
# Home Assistant auto-discovery, and Adafruit IO. Connect-publish-disconnect
# per interval to keep memory clean.
# ----------------------------------------------------------------------

import gc
import json

from .. import state, version
from ..helpers import log

try:
    import wifi
    import socketpool
except ImportError:
    wifi = None
    socketpool = None

try:
    import adafruit_minimqtt.adafruit_minimqtt as MQTT
    _HAS_MQTT = True
except Exception:
    MQTT = None
    _HAS_MQTT = False
    print("adafruit_minimqtt not available; MQTT/AIO disabled")


def _publish_one(broker, port, user, password, topics_payloads, use_ssl=False):
    """Connect, publish a list of (topic, payload, retain) tuples, disconnect."""
    if not _HAS_MQTT or MQTT is None:
        return False
    if wifi is None or not wifi.radio.connected:
        return False

    # Feed watchdog, GC, and reuse the global SocketPool (the ESP32-S3 has a
    # small fixed socket limit; a new pool per call burns them).
    gc.collect()
    if state._wd is not None:
        try:
            state._wd.feed()
        except Exception:
            pass
    pool = state.socket_pool
    if pool is None:
        if socketpool is not None:
            pool = socketpool.SocketPool(wifi.radio)
        else:
            return False
    try:
        client = MQTT.MQTT(
            broker=broker,
            port=int(port),
            username=user or None,
            password=password or None,
            socket_pool=pool,
            ssl_context=None,
            connect_retries=1,
            socket_timeout=5,
            keep_alive=15,
        )
        client.connect()
        for topic, payload, retain in topics_payloads:
            try:
                client.publish(topic, payload, retain=retain)
            except Exception as e:
                print("MQTT publish error:", topic, e)
        client.disconnect()
        gc.collect()
        return True
    except Exception as e:
        gc.collect()
        print("MQTT error:", e)
        return False


def publish_to_mqtt():
    """Publish current CO2/temp/RH to the configured MQTT broker."""
    s = state.settings
    broker = s.get("mqtt_broker", "").strip()
    if not broker:
        return False
    port = s.get("mqtt_port", 1883)
    user = s.get("mqtt_user", "")
    password = s.get("mqtt_pass", "")
    prefix = (s.get("mqtt_topic_prefix", "knowco2") or "knowco2").strip()
    topics = []
    if state.last_co2 is not None:
        topics.append(("%s/co2" % prefix, str(int(state.last_co2)), False))
    if state.last_temp_c is not None:
        topics.append(("%s/temp_c" % prefix, "%.2f" % state.last_temp_c, False))
    if state.last_rh is not None:
        topics.append(("%s/rh" % prefix, "%.2f" % state.last_rh, False))
    if not topics:
        return False
    publish_mqtt_discovery()
    ok = _publish_one(broker, port, user, password, topics)
    if ok:
        log("mqtt", "MQTT published to", broker, min_interval=30.0)
    else:
        log("mqtt_err", "MQTT publish failed to", broker, min_interval=30.0)


def publish_mqtt_discovery():
    """Publish Home Assistant MQTT discovery config (once per boot) so HA
    auto-discovers KnowCO2 sensors with no configuration.yaml editing."""
    if state.mqtt_discovery_sent:
        return
    s = state.settings
    broker = s.get("mqtt_broker", "").strip()
    if not broker:
        return
    port = s.get("mqtt_port", 1883)
    user = s.get("mqtt_user", "")
    pw = s.get("mqtt_pass", "")
    prefix = (s.get("mqtt_topic_prefix", "knowco2") or "knowco2").strip()
    uid = (state.hwid_hex or s.get("device_id", "co2-node-1") or "co2-node-1").lower()
    device = {
        "identifiers": ["knowco2_%s" % uid],
        "name": "KnowCO2",
        "manufacturer": "KNOWCO2 LLC",
        "model": "KnowCO2 Model A",
        "sw_version": version.FIRMWARE_VERSION,
    }
    sensors = [
        ("co2",    "CO2",         "ppm", "carbon_dioxide", "%s/co2"    % prefix),
        ("temp_c", "Temperature", "\u00b0C", "temperature",  "%s/temp_c" % prefix),
        ("rh",     "Humidity",    "%",   "humidity",       "%s/rh"     % prefix),
    ]
    topics = []
    for key, name, unit, dc, state_topic in sensors:
        cfg_topic = "homeassistant/sensor/knowco2_%s/%s/config" % (uid, key)
        payload = json.dumps({
            "name": "KnowCO2 %s" % name,
            "unique_id": "knowco2_%s_%s" % (uid, key),
            "state_topic": state_topic,
            "unit_of_measurement": unit,
            "device_class": dc,
            "device": device,
        })
        topics.append((cfg_topic, payload, True))
    ok = _publish_one(broker, port, user, pw, topics)
    if ok:
        state.mqtt_discovery_sent = True
        log("mqtt", "HA MQTT discovery published", min_interval=60.0)
    else:
        log("mqtt_err", "HA MQTT discovery failed", min_interval=60.0)


def publish_to_adafruit_io():
    """Publish current readings to Adafruit IO via MQTT."""
    s = state.settings
    aio_user = s.get("aio_username", "").strip()
    aio_key = s.get("aio_key", "").strip()
    if not aio_user or not aio_key:
        return False
    group = (s.get("aio_group_key", "knowco2") or "knowco2").strip()
    # Adafruit IO MQTT topic format: <username>/feeds/<group>.<feed>
    topics = []
    if state.last_co2 is not None:
        topics.append(("%s/feeds/%s.co2" % (aio_user, group), str(int(state.last_co2)), False))
    if state.last_temp_c is not None:
        topics.append(("%s/feeds/%s.temperature" % (aio_user, group), "%.2f" % state.last_temp_c, False))
    if state.last_rh is not None:
        topics.append(("%s/feeds/%s.humidity" % (aio_user, group), "%.2f" % state.last_rh, False))
    if not topics:
        return False
    ok = _publish_one("io.adafruit.com", 1883, aio_user, aio_key, topics)
    if ok:
        log("aio", "Adafruit IO published", min_interval=30.0)
    else:
        log("aio_err", "Adafruit IO publish failed", min_interval=30.0)
    return ok
