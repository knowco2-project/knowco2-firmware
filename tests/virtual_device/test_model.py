"""Model conformance and real candidate button regressions, without hardware."""
from pathlib import Path
import sys
import unittest

from model import Event, EventQueue, InputDevice, PINS, TICKS_PERIOD, replay

ROOT = Path(__file__).resolve().parents[2]


class InputHelpers:
    def device(self, **kw):
        d = InputDevice(ROOT, **kw)
        d.plug()
        return d

    def tap(self, d, key, ms=120, service=True):
        d.set_button(key, True)
        d.advance(ms, service=service)
        d.set_button(key, False)
        d.advance(30, service=service)


class ModelTests(InputHelpers, unittest.TestCase):
    def test_real_hardware_backend_and_polarities(self):
        d = self.device()
        self.assertEqual(d.buttons.backend, "keypad")
        self.assertEqual([s.events.capacity for s in d.scanners], [16, 32])
        for key, (pin, active) in PINS.items():
            d.set_button(key, True)
            self.assertEqual(d.levels[pin], active)
            d.set_button(key, False)
            self.assertEqual(d.levels[pin], not active)

    def test_scanner_runs_independently_of_poll(self):
        d = self.device()
        self.tap(d, "B", service=False)
        self.assertEqual(d.action_names(), [])
        self.assertEqual(len(d.scanners[1].events.items), 2)
        self.assertEqual(d.poll(), ["b_short"])

    def test_no_event_before_scan(self):
        d = self.device()
        d.set_button("B", True)
        d.advance(14, service=False)
        self.assertEqual(d.poll(), [])
        d.advance(1, service=False)
        self.assertEqual(d.poll(), ["b_short"])

    def test_short_pulse_between_scans_is_not_invented(self):
        d = self.device()
        d.schedule_button(1, "B", True)
        d.schedule_button(2, "B", False)
        d.advance(100)
        self.assertEqual(d.action_names(), [])

    def test_queue_overflow_matches_pinned_runtime(self):
        q = EventQueue(2)
        q.append((0, True, 1)); q.append((0, False, 2)); q.append((0, True, 3))
        self.assertTrue(q.overflowed)
        self.assertEqual(q.lost, 1)
        e = Event()
        self.assertTrue(q.get_into(e)); self.assertEqual(e.timestamp, 1)
        self.assertTrue(q.get_into(e)); self.assertEqual(e.timestamp, 2)
        self.assertFalse(q.get_into(e)); self.assertEqual(e.timestamp, 2)
        q.clear(); self.assertFalse(q.overflowed)

    def test_unknown_inputs_rejected(self):
        d = self.device()
        for fn in (lambda: d.set_button("D", True), lambda: d.set_button("A", 1),
                   lambda: d.advance(-1), lambda: d.advance(120001),
                   lambda: d.advance(1.5), lambda: d.schedule_button(-1, "A", True)):
            with self.assertRaises(ValueError): fn()

    def test_unsupported_held_boot_is_not_silently_passed(self):
        d = InputDevice(ROOT)
        d.set_button("A", True)
        with self.assertRaises(NotImplementedError): d.plug()

    def test_no_global_module_or_clock_pollution(self):
        before = {name: sys.modules.get(name) for name in ("keypad", "supervisor", "time")}
        a, b = self.device(), self.device()
        self.tap(a, "B")
        b.advance(200)
        self.assertEqual(a.action_names(), ["b_short"])
        self.assertEqual(b.action_names(), [])
        self.assertIsNot(a.module, b.module)
        self.assertIsNot(a.scanners[1].events, b.scanners[1].events)
        for name, value in before.items(): self.assertIs(sys.modules.get(name), value)

    def test_power_cut_discards_unconsumed_events(self):
        d = self.device()
        self.tap(d, "B", service=False)
        d.unplug()
        d.advance(100)
        self.assertEqual(d.poll(), [])
        d.plug(); d.advance(100)
        self.assertEqual(d.action_names(), [])
        self.assertEqual(d.epoch, 2)
        self.assertEqual(d.ticks_ms(), 100)

    def test_pin_activity_when_unpowered_does_not_execute_firmware(self):
        d = InputDevice(ROOT)
        self.tap(d, "B")
        self.assertEqual(d.action_names(), [])
        d.plug(); d.advance(100)
        self.assertEqual(d.action_names(), [])

    def test_timeline_is_reproducible(self):
        traces = []
        for _ in range(2):
            d = self.device()
            d.schedule_button(100, "B", True)
            d.schedule_button(220, "B", False)
            d.advance(8000, service=False)
            d.poll(); traces.append(d.trace)
        self.assertEqual(traces[0], traces[1])

    def test_replay_rejects_unknown_operation(self):
        with self.assertRaises(ValueError):
            replay(ROOT, {"id":"bad", "catalog_id":"VDA-003", "title":"bad",
                          "steps":[{"op":"set_screen"}], "expected_actions":[]})


class CandidateInputTests(InputHelpers, unittest.TestCase):
    # Keep product checks distinct from model checks in unittest output.
    def test_b_fires_on_press_and_not_again_on_release(self):
        d = self.device()
        d.set_button("B", True); d.advance(30)
        self.assertEqual(d.action_names(), ["b_short"])
        d.set_button("B", False); d.advance(30)
        self.assertEqual(d.action_names(), ["b_short"])

    def test_a_tap_and_hold_are_distinct(self):
        d = self.device()
        self.tap(d, "A")
        self.tap(d, "A", ms=2100)
        self.assertEqual(d.action_names(), ["a_short", "a_hold"])

    def test_b_hold_is_context_guarded(self):
        d = self.device(context="info")
        self.tap(d, "B", ms=2100)
        self.assertEqual(d.action_names(), ["b_hold"])
        d = self.device(context="main")
        self.tap(d, "B", ms=2100)
        self.assertEqual(d.action_names(), ["b_short"])

    def test_two_taps_survive_eight_second_stall(self):
        d = self.device()
        for t in (200, 900):
            d.schedule_button(t, "B", True)
            d.schedule_button(t + 120, "B", False)
        d.advance(8000, service=False)
        self.assertEqual(d.poll(), ["b_short", "b_short"])
        self.assertEqual([e["at_ms"] for e in d.delivered], [8000, 8000])

    def test_single_hold_completed_during_stall(self):
        d = self.device()
        d.schedule_button(100, "C", True); d.schedule_button(2200, "C", False)
        d.advance(8000, service=False)
        self.assertEqual(d.poll(), ["c_hold"])

    def test_complete_combo_during_stall_is_delivered_once(self):
        for first_release in ("A", "B"):
            with self.subTest(first_release=first_release):
                d = self.device()
                d.schedule_button(100, "A", True); d.schedule_button(160, "B", True)
                d.schedule_button(3300, first_release, False)
                d.schedule_button(3340, "B" if first_release == "A" else "A", False)
                d.advance(8000, service=False)
                self.assertEqual(d.poll(), ["ab_hold"])
                self.assertEqual(d.poll(), [])

    def test_short_combo_during_stall_does_not_unlock(self):
        d = self.device()
        d.schedule_button(100, "A", True); d.schedule_button(160, "B", True)
        d.schedule_button(1000, "A", False); d.schedule_button(1040, "B", False)
        d.advance(8000, service=False)
        self.assertEqual(d.poll(), [])

    def test_live_combo_release_does_not_duplicate_unlock(self):
        d = self.device()
        d.set_button("A", True); d.advance(30)
        d.set_button("B", True); d.advance(3200)
        self.assertEqual(d.action_names(), ["ab_hold"])
        d.set_button("B", False); d.set_button("A", False); d.advance(100)
        self.assertEqual(d.action_names(), ["ab_hold"])

    def test_overflow_cannot_convert_released_short_combo_into_unlock(self):
        d = self.device()
        d.set_button("A", True); d.advance(30)
        d.set_button("B", True); d.advance(30)
        d.set_button("A", False); d.set_button("B", False)
        d.advance(30, service=False)
        for _ in range(20):
            self.tap(d, "C", ms=30, service=False)
        d.advance(4000, service=False)
        self.assertEqual(d.poll(), [])
        self.assertEqual(d.buttons.dropped, 1)
        self.assertEqual(d.buttons.down, [False, False, False])
        self.tap(d, "B")
        self.assertEqual(d.action_names(), ["b_short"])

    def test_single_hold_across_supervisor_wrap(self):
        d = self.device(tick_offset=TICKS_PERIOD - 100)
        self.tap(d, "C", ms=2100, service=False)
        self.assertEqual(d.poll(), ["c_hold"])

    def test_complete_combo_across_wrap(self):
        d = self.device(tick_offset=TICKS_PERIOD - 200)
        d.schedule_button(30, "A", True); d.schedule_button(90, "B", True)
        d.schedule_button(3300, "A", False); d.schedule_button(3340, "B", False)
        d.advance(4000, service=False)
        self.assertEqual(d.poll(), ["ab_hold"])

    def test_hard_power_cut_during_hold_does_not_resume_old_gesture(self):
        d = self.device()
        d.set_button("C", True); d.advance(1000)
        d.unplug(); d.set_button("C", False); d.advance(3000)
        d.plug(); d.advance(3000)
        self.assertEqual(d.action_names(), [])


if __name__ == "__main__":
    unittest.main()
