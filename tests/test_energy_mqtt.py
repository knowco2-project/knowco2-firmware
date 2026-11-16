"""Exercise the actual MQTT publisher using the existing TLS test transport."""
import json
import types
import unittest
from test_mqtt_tls import _load_module


class EnergyMqttTests(unittest.TestCase):
    def load(self, snapshot=None):
        m,s,ssl,mini,_ = _load_module()
        s.settings = {"mqtt_broker":"broker.example", "mqtt_user":"user",
                      "mqtt_pass":"secret", "mqtt_topic_prefix":"knowco2"}
        if snapshot is not None:
            s.energy_monitor = types.SimpleNamespace(snapshot=snapshot)
        return m,s,ssl,mini

    def test_default_unchanged(self):
        m,_,ssl,mini=self.load(); self.assertTrue(m.publish_to_mqtt())
        self.assertEqual(len(mini.instances),1)
        self.assertEqual(len(mini.instances[0].published),3)
        self.assertIs(mini.instances[0].kwargs['ssl_context'],ssl.context)

    def test_single_nonretained_payload_after_co2_same_connection(self):
        m,_,_,mini=self.load(lambda:{'power_mw_est':400,'energy_mwh_est':1200})
        self.assertTrue(m.publish_to_mqtt())
        self.assertEqual(len(mini.instances),1)
        msgs=mini.instances[0].published
        self.assertEqual([x[0] for x in msgs[:3]],['knowco2/co2','knowco2/temp_c','knowco2/rh'])
        self.assertEqual(msgs[3][0],'knowco2/012345/energy')
        self.assertFalse(msgs[3][2])
        self.assertEqual(json.loads(msgs[3][1])['power_mw_est'],400)

    def test_observer_failure_does_not_stop_co2_publication(self):
        def bad(): raise MemoryError('injected')
        m,s,_,mini=self.load(bad); self.assertTrue(m.publish_to_mqtt())
        self.assertIsNone(s.energy_monitor)
        self.assertEqual(len(mini.instances[0].published),3)

    def test_oversized_payload_disables_observer_not_co2(self):
        m,s,_,mini=self.load(lambda:{'oversize':'x'*1025})
        self.assertTrue(m.publish_to_mqtt()); self.assertIsNone(s.energy_monitor)
        self.assertEqual(len(mini.instances[0].published),3)

    def test_no_extra_upload_without_co2_environment_payload(self):
        def bad(): raise AssertionError('not called')
        m,s,_,mini=self.load(bad); s.last_co2=s.last_temp_c=s.last_rh=None
        self.assertFalse(m.publish_to_mqtt()); self.assertEqual(len(mini.instances),0)

    def test_existing_discovery_connection_count_unchanged(self):
        m,s,_,mini=self.load(lambda:{'energy_mwh_est':1})
        s.mqtt_discovery_sent=False; self.assertTrue(m.publish_to_mqtt())
        self.assertEqual(len(mini.instances),2)
        self.assertEqual(len(mini.instances[0].published),3)
        self.assertEqual(len(mini.instances[1].published),4)

    def test_credentials_still_require_tls(self):
        m,s,_,mini=self.load(lambda:{'energy_mwh_est':1})
        s.settings['mqtt_use_tls']=False
        self.assertFalse(m.publish_to_mqtt()); self.assertEqual(mini.instances,[])


if __name__ == '__main__': unittest.main()
