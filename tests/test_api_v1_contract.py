import unittest

from knowco2.web.api_v1_contract import (
    API_SCHEMA_VERSION,
    info_payload,
    normalize_settings_patch,
    safe_settings,
    settings_payload,
    setup_qr_payload,
    wifi_write_payload,
)


class LocalApiV1ContractTests(unittest.TestCase):
    def test_safe_settings_never_exposes_secrets(self):
        settings = {
            "temp_mode": "F",
            "display_mode": 1,
            "cloud_interval_sec": 60,
            "sta_password": "private-wifi-password",
            "ap_password": "temporary-ap-password",
            "cloud_device_token": "permanent-cloud-secret",
            "admin_password": "admin-secret",
        }
        result = safe_settings(settings)
        self.assertEqual(result["temp_mode"], "F")
        self.assertEqual(result["display_mode"], 1)
        self.assertNotIn("sta_password", result)
        self.assertNotIn("ap_password", result)
        self.assertNotIn("cloud_device_token", result)
        self.assertNotIn("admin_password", result)

    def test_info_explicitly_says_native_app_not_required(self):
        result = info_payload(device_id="KC2-TEST01", firmware_version="10.2.1")
        self.assertEqual(result["schema_version"], API_SCHEMA_VERSION)
        self.assertTrue(result["capabilities"]["browser_setup"])
        self.assertFalse(result["capabilities"]["native_app_required"])

    def test_settings_payload_is_versioned(self):
        result = settings_payload({"temp_mode": "C"}, settings_version=7)
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["settings_version"], 7)
        self.assertEqual(result["settings"]["temp_mode"], "C")

    def test_settings_patch_normalizes_supported_values(self):
        result = normalize_settings_patch({
            "temp_mode": "c",
            "display_mode": "2",
            "alerts_enabled": 1,
            "low_threshold": "800",
            "med_threshold": "1200",
            "alert_threshold": "1500",
            "cloud_interval_sec": "30",
        })
        self.assertEqual(result["temp_mode"], "C")
        self.assertEqual(result["display_mode"], 2)
        self.assertTrue(result["alerts_enabled"])
        self.assertEqual(result["cloud_interval_sec"], 30)

    def test_unknown_or_sensitive_settings_are_rejected(self):
        with self.assertRaises(ValueError):
            normalize_settings_patch({"made_up_setting": True})
        with self.assertRaises(ValueError):
            normalize_settings_patch({"sta_password": "secret123"})

    def test_wifi_credentials_use_dedicated_write_only_contract(self):
        result = wifi_write_payload("My Network", "password123")
        self.assertEqual(result["ssid"], "My Network")
        self.assertEqual(result["password"], "password123")
        with self.assertRaises(ValueError):
            wifi_write_payload("My Network", "short")

    def test_setup_qr_contains_no_cloud_secret(self):
        result = setup_qr_payload(
            "KC2-TEST01",
            "knowco2-a1b2",
            "temporary123",
            pair_code="ABCD1234",
        )
        self.assertEqual(result["type"], "knowco2-setup")
        self.assertEqual(result["local_url"], "http://192.168.4.1")
        self.assertNotIn("device_secret", result)
        self.assertNotIn("cloud_device_token", result)


if __name__ == "__main__":
    unittest.main()
