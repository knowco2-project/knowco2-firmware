import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from knowco2.web.api_v1_contract import (
    API_SCHEMA_VERSION,
    activation_request_payload,
    info_payload,
    normalize_activation_response,
    normalize_claim_request,
    normalize_settings_patch,
    safe_settings,
    settings_payload,
    setup_qr_payload,
    wifi_write_payload,
)

VALID_DEVICE_SECRET = "QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE="


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
        result = info_payload(
            device_id="KC2-TEST01",
            cloud_device_id="cloud-123",
            firmware_version="10.2.1",
        )
        self.assertEqual(result["schema_version"], API_SCHEMA_VERSION)
        self.assertTrue(result["capabilities"]["browser_setup"])
        self.assertFalse(result["capabilities"]["native_app_required"])
        self.assertEqual(result["cloud"]["device_id"], "cloud-123")
        serialized = repr(result).lower()
        self.assertNotIn("pair_code", serialized)
        self.assertNotIn("device_secret", serialized)
        self.assertNotIn("hardware_id", serialized)

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
        with self.assertRaises(ValueError):
            normalize_settings_patch({"cloud_enabled": True})

    def test_settings_boolean_strings_are_strict(self):
        self.assertFalse(normalize_settings_patch({"alerts_enabled": "false"})["alerts_enabled"])
        self.assertTrue(normalize_settings_patch({"alerts_enabled": "true"})["alerts_enabled"])
        with self.assertRaises(ValueError):
            normalize_settings_patch({"alerts_enabled": "yes"})

    def test_wifi_credentials_use_dedicated_write_only_contract(self):
        result = wifi_write_payload("My Network", "password123")
        self.assertEqual(result["ssid"], "My Network")
        self.assertEqual(result["password"], "password123")
        with self.assertRaises(ValueError):
            wifi_write_payload("My Network", "short")
        with self.assertRaises(ValueError):
            wifi_write_payload("Open Network", "")

    def test_claim_code_normalization_uses_cloud_alphabet(self):
        self.assertEqual(
            normalize_claim_request({"claim_code": "abcd-efgh"}),
            {"claim_code": "ABCDEFGH"},
        )
        with self.assertRaises(ValueError):
            normalize_claim_request({"claim_code": "ABCD-1FGH"})
        with self.assertRaises(ValueError):
            normalize_claim_request({
                "claim_code": "ABCDEFGH",
                "activation_token": "x" * 32,
            })
        self.assertEqual(
            normalize_claim_request({"activation_token": "Ab9_-" + "x" * 27})["activation_token"],
            "Ab9_-" + "x" * 27,
        )
        with self.assertRaises(ValueError):
            normalize_claim_request({"activation_token": "x" * 31 + "+"})

    def test_activation_contract_is_idempotent_and_secret_is_validated(self):
        request = activation_request_payload(
            temporary_credential={"claim_code": "ABCD-EFGH"},
            hardware_id="AABBCCDD",
            board_id="reverse-tft",
            serial="KC2-2026-0001-7",
            firmware_version="v0.0.9",
            request_id="A" * 64,
        )
        self.assertEqual(request["claim_code"], "ABCDEFGH")
        self.assertEqual(request["request_id"], "A" * 64)
        self.assertNotIn("activation_token", request)

        response = normalize_activation_response({
            "schema_version": 1,
            "device_id": "cloud-device-123",
            "device_secret": VALID_DEVICE_SECRET,
            "cloud_api_url": "https://api.knowco2.com",
        })
        self.assertEqual(response["device_id"], "cloud-device-123")
        with self.assertRaises(ValueError):
            normalize_activation_response({
                "schema_version": 1,
                "device_id": "cloud-device-123",
                "device_secret": "short",
            })
        with self.assertRaises(ValueError):
            normalize_activation_response({
                "schema_version": 1,
                "device_id": "cloud-device-123",
                "device_secret": "A" * 44,
            })
        with self.assertRaises(ValueError):
            normalize_activation_response({
                "schema_version": 1,
                "device_id": "cloud-device-123",
                "device_secret": "!" + VALID_DEVICE_SECRET[1:],
            })
        with self.assertRaises(ValueError):
            normalize_activation_response({
                "schema_version": 1,
                "device_id": "cloud-device-123",
                "device_secret": VALID_DEVICE_SECRET,
                "cloud_api_url": "https://attacker.example",
            })

    def test_setup_qr_contains_no_cloud_secret(self):
        result = setup_qr_payload(
            "KC2-TEST01",
            "knowco2-a1b2",
            "temporary123",
        )
        self.assertEqual(result["type"], "knowco2-setup")
        self.assertEqual(result["local_url"], "http://192.168.4.1")
        self.assertNotIn("device_secret", result)
        self.assertNotIn("cloud_device_token", result)
        self.assertNotIn("pair_code", result)


if __name__ == "__main__":
    unittest.main()
