#!/usr/bin/env python3
"""Pixel-bounds regressions for normal and Big CO2 text views.

Execute the real screens.py functions, not copies of their implementations.
The fake label models the documented unscaled bounding_box and anchor contract,
including nonzero glyph bearings. This is a layout test, not an LCD emulator;
physical appearance and real-font verification remain hardware acceptance items.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "knowco2" / "ui" / "screens.py"


class Label:
    def __init__(self, text="", scale=1, anchor=(0.5, 0.5), position=(120, 67)):
        self._text = text
        self._scale = scale
        self.anchor_point = anchor
        self.anchored_position = position
        self.hidden = False
        self.color = 0x123456
        self.char_width = 6
        self.glyph_height = 12
        self.bearing = (-1, -7)
        self.text_writes = 0
        self.scale_writes = 0

    @property
    def text(self):
        return self._text

    @text.setter
    def text(self, value):
        self.text_writes += 1
        self._text = value

    @property
    def scale(self):
        return self._scale

    @scale.setter
    def scale(self, value):
        if not isinstance(value, int) or value < 1:
            raise ValueError("Label scale must be a positive integer")
        self.scale_writes += 1
        self._scale = value

    @property
    def bounding_box(self):
        return (*self.bearing, len(self.text) * self.char_width,
                self.glyph_height if self.text else 0)

    @property
    def rect(self):
        bx, by, width, height = self.bounding_box
        ax, ay = self.anchor_point
        px, py = self.anchored_position
        # Integer displayio group translation, with bearings included.
        x = int(px - bx * self.scale - round(ax * width * self.scale)) + bx * self.scale
        y = int(py - by * self.scale - round(ay * height * self.scale)) + by * self.scale
        return (x, y, x + width * self.scale, y + height * self.scale)


def overlaps(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


class UiLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
        cls.code = compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE), "exec")

    def setUp(self):
        self.config = SimpleNamespace(SCREEN_MAIN=0, SCREEN_APINFO=1,
                                      SCREEN_REGULATORY=2, BATT_WARN_PCT=15,
                                      TREND_LOOKBACK_SECONDS=30,
                                      SCD_MEASUREMENT_PERIOD=5, TREND_DEADBAND=10)
        self.state = SimpleNamespace(screen=0, display_mode=0, last_co2=814,
                                     last_temp_c=22.5, last_rh=45.0, temp_mode="F",
                                     energy_mode=False, rate_of_change=None,
                                     sensor_frozen_shown=False, fuel_gauge=object(),
                                     cached_pct=80, last_co2_prev=None, co2_history=[],
                                     alerts_enabled=True, LOW_THRESHOLD=800,
                                     MED_THRESHOLD=1200, active_scheme={
                                         "low": 0x00FF00, "med": 0xFFFF00,
                                         "alert": 0xFF0000},
                                     qr_tilegrid_wifi=None, qr_tilegrid_url=None,
                                     qr_caption1=None, qr_caption2=None,
                                     qr_page_indicator=None)
        self.widgets = SimpleNamespace(display=SimpleNamespace(width=240, height=135))
        for name in (
            "co2_label", "th_label", "ppm_label", "sensor_frozen_label", "lp_badge_label",
            "batt_warn_label", "graph", "y_min_label", "y_max_label", "x_left_label",
            "x_right_label", "x_mid_label", "low_label", "med_label", "high_label",
            "graph_value_label", "ap_ssid_label", "ap_pass_label", "ap_ip_label",
            "ap_batt_label", "ap_hw_label", "ap_scd_label", "ap_fw_label",
        ):
            setattr(self.widgets, name, Label())
        self.widgets._REG_LABELS = [Label(), Label()]
        self.widgets.co2_label.text = "CO2: ---- ppm"
        self.widgets.co2_label.scale = 3
        self.widgets.co2_label.anchored_position = (120, 47)
        self.widgets.th_label.text = "--.-F  --.-%"
        self.widgets.th_label.scale = 2
        self.widgets.th_label.anchored_position = (120, 89)
        self.widgets.batt_warn_label.text = "!! BATT 11%"
        self.widgets.batt_warn_label.anchor_point = (0.5, 1.0)
        self.widgets.batt_warn_label.anchored_position = (120, 123)
        self.widgets.sensor_frozen_label.text = "!! SENSOR ERR"
        self.widgets.sensor_frozen_label.anchor_point = (0.5, 1.0)
        self.widgets.sensor_frozen_label.anchored_position = (120, 133)
        self.env = dict(W=self.widgets, state=self.state, config=self.config)
        exec(self.code, self.env)

    def refresh(self, value=814, mode=0):
        self.state.last_co2 = value
        self.state.display_mode = mode
        self.env["update_visibility"]()
        self.env["refresh_text"]()

    def assert_inside(self, label, top=0, bottom=135):
        x1, y1, x2, y2 = label.rect
        self.assertGreaterEqual(x1, 0, label.rect)
        self.assertLessEqual(x2, 240, label.rect)
        self.assertGreaterEqual(y1, top, label.rect)
        self.assertLessEqual(y2, bottom, label.rect)

    def assert_clear(self):
        reading = self.widgets.co2_label
        self.assert_inside(reading, top=16)
        battery = self.widgets.batt_warn_label
        sensor = self.widgets.sensor_frozen_label
        for warning in (battery, sensor):
            self.assert_inside(warning)
            self.assertFalse(overlaps(reading.rect, warning.rect),
                             (reading.rect, warning.rect))
        self.assertFalse(overlaps(battery.rect, sensor.rect))
        if self.state.display_mode == 0:
            env = self.widgets.th_label
            self.assert_inside(env, top=16)
            for other in (reading, battery, sensor):
                self.assertFalse(overlaps(env.rect, other.rect), (env.rect, other.rect))

    def test_photo_14703_full_line_fits(self):
        self.refresh(14703, 0)
        self.assertEqual(self.widgets.co2_label.text, "CO2: 14703 ppm")
        self.assert_clear()
        self.assertEqual(self.widgets.co2_label.scale, 2)

    def test_photo_814_low_battery_is_separate(self):
        self.widgets.co2_label.glyph_height = 8
        self.state.cached_pct = 11
        self.refresh(814, 1)
        self.assertEqual(self.widgets.co2_label.text, "814")
        self.assertFalse(self.widgets.batt_warn_label.hidden)
        self.assert_clear()

    def test_digit_boundaries_both_modes(self):
        for mode in (0, 1):
            for value in (0, 9, 10, 99, 100, 814, 999, 1000, 9999, 10000,
                          14703, 40000, 65535, 999999):
                with self.subTest(mode=mode, value=value):
                    self.refresh(value, mode)
                    self.assertEqual(self.widgets.co2_label.text,
                                     str(value) if mode == 1 else "CO2: %d ppm" % value)
                    self.assert_clear()

    def test_larger_and_smaller_glyph_bounds(self):
        for width, height in ((6, 8), (6, 12), (8, 14)):
            for name in ("co2_label", "th_label", "batt_warn_label", "sensor_frozen_label"):
                label = getattr(self.widgets, name)
                label.char_width, label.glyph_height = width, height
            for mode in (0, 1):
                for value in (9, 814, 9999, 14703, 40000):
                    with self.subTest(width=width, height=height, mode=mode, value=value):
                        self.refresh(value, mode)
                        self.assert_clear()

    def test_scale_recovers_when_value_shrinks(self):
        self.refresh(14703, 0)
        self.assertEqual(self.widgets.co2_label.scale, 2)
        self.refresh(814, 0)
        self.assertEqual(self.widgets.co2_label.scale, 3)
        self.assert_clear()

    def test_visibility_refresh_does_not_restore_oversized_scale(self):
        self.refresh(14703, 0)
        self.env["update_visibility"]()
        self.assertEqual(self.widgets.co2_label.scale, 2)
        self.assert_clear()

    def test_mode_switches_keep_labels_separate(self):
        for mode in (1, 0, 1, 2, 0, 1):
            self.refresh(14703, mode)
            if mode in (0, 1):
                self.assert_clear()
                self.assertEqual(self.widgets.th_label.hidden, mode == 1)
            else:
                self.assertTrue(self.widgets.co2_label.hidden)

    def test_warning_can_appear_between_sensor_refreshes(self):
        self.refresh(814, 1)
        rect = self.widgets.co2_label.rect
        # Match code.py's independent battery refresh; no layout call here.
        self.state.cached_pct = 11
        self.widgets.batt_warn_label.text = "!! BATT 11%"
        self.widgets.batt_warn_label.hidden = False
        self.assert_clear()
        self.assertEqual(rect, self.widgets.co2_label.rect)

    def test_warning_toggle_does_not_resize_reading(self):
        self.refresh(814, 1)
        initial = self.widgets.co2_label.rect
        for pct in (11, 0, 100, None, 11):
            self.state.cached_pct = pct
            self.env["update_visibility"]()
            self.assertEqual(initial, self.widgets.co2_label.rect)
            self.assert_clear()

    def test_sensor_and_battery_warnings_have_distinct_rows(self):
        self.state.cached_pct = 11
        self.state.sensor_frozen_shown = True
        for mode in (0, 1):
            self.refresh(14703, mode)
            self.assertFalse(self.widgets.batt_warn_label.hidden)
            self.assertFalse(self.widgets.sensor_frozen_label.hidden)
            self.assert_clear()

    def test_missing_reading_placeholder_is_fitted(self):
        for mode in (0, 1):
            self.refresh(None, mode)
            self.assertEqual(self.widgets.co2_label.text,
                             "----" if mode else "CO2: ---- ppm")
            self.assert_clear()

    def test_missing_temperature_is_fitted(self):
        self.state.last_temp_c = None
        for mode in (0, 1):
            self.refresh(14703, mode)
            self.assert_clear()

    def test_temperature_and_optional_humidity(self):
        for unit in ("F", "C"):
            self.state.temp_mode = unit
            for temperature, humidity in ((-40, 100), (85, 99.9), (22.5, None)):
                self.state.last_temp_c = temperature
                self.state.last_rh = humidity
                self.refresh(14703, 0)
                self.assertIn(unit, self.widgets.th_label.text)
                self.assert_clear()

    def test_colours_and_reading_are_not_changed_by_layout(self):
        self.refresh(14703, 0)
        self.env["apply_alert_colors"](14703)
        self.assertEqual(self.widgets.co2_label.color, 0xFF0000)
        self.env["_layout_main_reading"]()
        self.assertEqual(self.widgets.co2_label.color, 0xFF0000)
        self.assertEqual(self.widgets.co2_label.text, "CO2: 14703 ppm")
        self.assertEqual(self.state.last_co2, 14703)

    def test_unchanged_reading_does_not_rebuild_glyphs_or_scale(self):
        for mode in (0, 1):
            self.refresh(814, mode)
            labels = (self.widgets.co2_label, self.widgets.th_label)
            before = [(x.text_writes, x.scale_writes, id(x)) for x in labels]
            self.env["refresh_text"]()
            self.env["update_visibility"]()
            self.assertEqual(before, [(x.text_writes, x.scale_writes, id(x)) for x in labels])

    def test_low_power_badge_visibility_has_no_stale_graph_state(self):
        self.state.energy_mode = True
        self.refresh(814, 1)
        self.assertFalse(self.widgets.lp_badge_label.hidden)
        self.refresh(814, 2)
        self.assertTrue(self.widgets.lp_badge_label.hidden)
        self.refresh(814, 0)
        self.assertFalse(self.widgets.lp_badge_label.hidden)

    def test_non_main_screens_hide_readings_and_warnings(self):
        self.state.cached_pct = 11
        self.state.sensor_frozen_shown = True
        self.refresh(14703, 1)
        for screen in (1, 2):
            self.state.screen = screen
            self.env["update_visibility"]()
            self.env["refresh_text"]()
            for name in ("co2_label", "th_label", "batt_warn_label", "sensor_frozen_label"):
                self.assertTrue(getattr(self.widgets, name).hidden, name)


if __name__ == "__main__":
    unittest.main()
