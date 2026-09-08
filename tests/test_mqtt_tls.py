"""Security regression tests for MQTT and Adafruit IO transport."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_module():
    for name in list(sys.modules):
        if name == "wifi" or name == "socketpool" or name == "ssl" or name.startswith("knowco2") or name.startswith("adafruit_minimqtt"):
            sys.modules.pop(name, None)

    pkg = types.ModuleType("knowco2")
    pkg.__path__ = [str(REPO_ROOT / "knowco2")]
    sys.modules["knowco2"] = pkg

    telemetry = types.ModuleType("knowco2.telemetry")
    telemetry.__path__ = [str(REPO_ROOT / "knowco2" / "telemetry")]
    sys.modules["knowco2.telemetry"] = telemetry

    state = types.ModuleType("knowco2.state")
    state.settings = {}
    state._wd = None
    state.socket_pool = object()
    state.last_co2 = 650
    state.last_temp_c = 22.5
    state.last_rh = 45.0
    state.mqtt_discovery_sent = True
    state.hwid_hex = "012345"
    sys.modules["knowco2.state"] = state
    pkg.state = state

    version = types.ModuleType("knowco2.version")
    version.FIRMWARE_VERSION = "test"
    sys.modules["knowco2.version"] = version
    pkg.version = version

    helpers = types.ModuleType("knowco2.helpers")
    helpers.messages = []
    helpers.log = lambda *args, **kwargs: helpers.messages.append(args)
    sys.modules["knowco2.helpers"] = helpers

    wifi = types.ModuleType("wifi")
    wifi.radio = types.SimpleNamespace(connected=True)
    sys.modules["wifi"] = wifi

    socketpool = types.ModuleType("socketpool")
    socketpool.SocketPool = lambda radio: object()
    sys.modules["socketpool"] = socketpool

    ssl = types.ModuleType("ssl")
    ssl.context = object()
    ssl.create_default_context = lambda: ssl.context
    sys.modules["ssl"] = ssl

    mini_pkg = types.ModuleType("adafruit_minimqtt")
    mini_pkg.__path__ = []
    sys.modules["adafruit_minimqtt"] = mini_pkg
    mini = types.ModuleType("adafruit_minimqtt.adafruit_minimqtt")
    mini.instances = []

    class _Client:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.published = []
            mini.instances.append(self)

        def connect(self):
            pass

        def publish(self, topic, payload, retain=False):
            self.published.append((topic, payload, retain))

        def disconnect(self):
            pass

    mini.MQTT = _Client
    sys.modules["adafruit_minimqtt.adafruit_minimqtt"] = mini

    path = REPO_ROOT / "knowco2" / "telemetry" / "mqtt.py"
    spec = importlib.util.spec_from_file_location("knowco2.telemetry.mqtt", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, state, ssl, mini, helpers


class MqttTlsTests(unittest.TestCase):
    def test_publish_one_uses_validating_context_when_tls_requested(self):
        mqtt, _, ssl, mini, _ = _load_module()
        ok = mqtt._publish_one("broker.example", 8883, "user", "secret", (("t", "v", False),), use_ssl=True)
        self.assertTrue(ok)
        self.assertIs(mini.instances[-1].kwargs["ssl_context"], ssl.context)

    def test_adafruit_io_is_fixed_to_tls_port(self):
        mqtt, state, _, _, _ = _load_module()
        state.settings = {"aio_username": "owner", "aio_key": "secret", "aio_group_key": "air"}
        calls = []
        mqtt._publish_one = lambda *args, **kwargs: calls.append((args, kwargs)) or True
        self.assertTrue(mqtt.publish_to_adafruit_io())
        args, kwargs = calls[-1]
        self.assertEqual(args[0:2], ("io.adafruit.com", 8883))
        self.assertTrue(kwargs["use_ssl"])

    def test_credentialed_generic_mqtt_defaults_to_tls(self):
        mqtt, state, _, _, _ = _load_module()
        state.settings = {
            "mqtt_broker": "broker.example", "mqtt_user": "user",
            "mqtt_pass": "secret", "mqtt_topic_prefix": "knowco2",
        }
        calls = []
        mqtt._publish_one = lambda *args, **kwargs: calls.append((args, kwargs)) or True
        self.assertTrue(mqtt.publish_to_mqtt())
        args, kwargs = calls[-1]
        self.assertEqual(args[1], 8883)
        self.assertTrue(kwargs["use_ssl"])

    def test_plaintext_mode_refuses_credentials(self):
        mqtt, state, _, _, helpers = _load_module()
        state.settings = {
            "mqtt_broker": "192.168.1.20", "mqtt_port": 1883,
            "mqtt_use_tls": False, "mqtt_user": "user", "mqtt_pass": "secret",
        }
        self.assertFalse(mqtt.publish_to_mqtt())
        self.assertTrue(any("plaintext" in " ".join(map(str, msg)).lower() for msg in helpers.messages))


if __name__ == "__main__":
    unittest.main()
