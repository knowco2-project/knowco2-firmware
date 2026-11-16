"""Deterministic tests of actual optional firmware, not an electrical simulator."""
import ast
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from knowco2 import energy as e
from knowco2.energy_monitor import Monitor, BUILD_ID


class Clock:
    def __init__(self):
        self.ms = 0
    def ticks(self):
        return self.ms & e.TICKS_MASK
    def time(self):
        return 1794859200 + self.ms // 1000
    def localtime(self, stamp):
        import time
        return time.gmtime(stamp)


def fixture(profile=True):
    clock = Clock()
    state = types.SimpleNamespace(
        energy_mode=False, _scd_period_effective=5.0, wifi_mode="STA",
        display_mode=0, screen=0, board_id_str="test-board",
        sensor_model_str="SCD41", sensor=object(), scd_init_failed=False,
        sensor_frozen_shown=False, last_http_ts=0, ntp_synced=False)
    board = types.SimpleNamespace(DISPLAY=types.SimpleNamespace(brightness=1.0))
    version = types.SimpleNamespace(FIRMWARE_VERSION="test", CP_VERSION="test-cp")
    p = {
        "id": "synthetic-test-only", "reference": "not-a-real-measurement",
        "adapter": "synthetic", "boundary": "wall_ac",
        "battery_absent_confirmed": True, "build_id": BUILD_ID,
        "board_id": "test-board", "sensor_model": "SCD41",
        "firmware": "test", "circuitpython": "test-cp",
        "modes": {(False, 5000, 1000, "STA", True, 0, 0, "idle"): 400,
                  (False, 5000, 1000, "STA", True, 0, 0, "mqtt"): 800},
    } if profile else None
    return clock, state, board, version, p


def monitor_fixture(profile=True):
    clock, state, board, version, p = fixture(profile)
    monitor = Monitor(state, board, version, p, clock.ticks, clock,
                      types.SimpleNamespace(connected=True))
    return monitor, clock, state, board


class AccumulatorTests(unittest.TestCase):
    def test_one_hour_and_fractional_remainders(self):
        a = e.Accumulator(); a.step(0, 401)
        for t in range(1000, 3600001, 1000):
            a.step(t, 401)
        self.assertEqual(a.values[e.E], 401)
        self.assertEqual(a.values[e.REM], 0)
        self.assertEqual(a.average_mw, 401)

    def test_sub_milliwatt_hour_not_rounded_away(self):
        a = e.Accumulator(); a.step(0, 1)
        for t in range(1, 1001):
            a.step(t, 1)
        self.assertEqual(a.values[e.E], 0)
        self.assertEqual(a.values[e.REM], 1000)

    def test_old_state_before_transition_no_double_count(self):
        a = e.Accumulator(); a.step(0, 400); a.step(10000, 800); a.step(20000, 400)
        self.assertEqual(a.values[e.E] * e.UJ_PER_MWH + a.values[e.REM], 12000000)

    def test_zero_is_known_but_none_is_unknown(self):
        a = e.Accumulator(); a.step(0, 0); a.step(1000, None); a.step(2000, 0)
        self.assertEqual((a.values[e.COVERED_S], a.values[e.UNKNOWN_S]), (1, 1))

    def test_gap_is_not_backfilled(self):
        a = e.Accumulator(); a.step(0, 400); a.step(e.MAX_GAP_MS+1, 400)
        self.assertEqual(a.values[e.E], 0)
        self.assertEqual(a.values[e.FLAGS], e.GAP)
        self.assertIsNone(a.average_mw)

    def test_discard_ambiguous_transition(self):
        a = e.Accumulator(); a.step(0, 400); a.step(1000, 800, discard=True)
        self.assertEqual(a.values[e.UNKNOWN_S], 1)
        self.assertEqual(a.values[e.E], 0)

    def test_wrap_and_invalid_clock_latches(self):
        a = e.Accumulator(); a.step(e.TICKS_PERIOD-500, 400); a.step(500, 400)
        self.assertEqual(a.values[e.COVERED_S], 1)
        a.step(400, 400); a.step(1500, 400)
        self.assertIsNone(a.snapshot()["energy_mwh_est"])
        self.assertEqual(a.values[e.FLAGS], e.CLOCK_INVALID)

    def test_rejects_invalid_power_and_ticks(self):
        for power in (-1, 5001, True, 4.2, "400"):
            with self.subTest(power=power), self.assertRaises(ValueError):
                e.Accumulator().step(0, power)
        for tick in (-1, e.TICKS_PERIOD, True, 2.5):
            with self.subTest(tick=tick), self.assertRaises(ValueError):
                e.Accumulator().step(tick, 400)

    def test_counter_saturation_is_unavailable(self):
        a = e.Accumulator(); a.step(0, 5000); a.values[e.E] = e.LIMIT
        a.step(1000, 5000)
        self.assertTrue(a.values[e.FLAGS] & e.SATURATED)
        self.assertIsNone(a.snapshot()["energy_mwh_est"])

    def test_time_saturation_is_unavailable(self):
        a = e.Accumulator(); a.step(0, 1); a.values[e.COVERED_S] = e.LIMIT
        a.step(1000, 1)
        self.assertIsNone(a.snapshot()["energy_mwh_est"])

    def test_averaging_includes_bursts_not_just_publish_instant(self):
        a = e.Accumulator(); a.step(0, 400); a.step(50000, 800); a.step(60000, 400)
        self.assertEqual(a.average_mw, 466)

    def test_unknown_resets_power_average(self):
        a = e.Accumulator(); a.step(0, 400); a.step(60000, 400)
        a.step(61000, None)
        self.assertIsNone(a.average_mw)
        a.step(62000, 400); a.step(122000, 400)
        self.assertEqual(a.average_mw, 400)

    def test_maximum_window_intermediates_stay_small(self):
        a = e.Accumulator(); a.step(0, 5000); a.step(59999, 5000)
        a.step(179999, 5000)
        self.assertEqual(a.average_mw, 5000)
        self.assertLess(5000 * 179999, e.LIMIT)

    def test_90_days_fixed_storage_and_exact_total(self):
        a = e.Accumulator(); a.step(0, 400)
        storage = a.values
        for n in range(1, 90*24*60+1):
            a.step((n*60000) & e.TICKS_MASK, 400)
        self.assertIs(storage, a.values)
        self.assertEqual(len(storage) * storage.itemsize, 36)
        self.assertEqual(a.values[e.E], 400*90*24)
        self.assertEqual(a.values[e.FLAGS], 0)

    def test_snapshot_does_not_advance_or_mutate(self):
        a = e.Accumulator(); a.step(0, 400); a.step(1000, 400)
        before = list(a.values)
        for _ in range(100): a.snapshot()
        self.assertEqual(list(a.values), before)

    def test_irregular_intervals_match_exact_integral(self):
        a = e.Accumulator(); a.step(0, 123); t = 0; exact = 0; old = 123
        for n in range(1000):
            dt = 1 + (n*977) % 5000; t += dt; exact += old*dt
            old = n % 5001; a.step(t, old)
        self.assertEqual(a.values[e.E]*e.UJ_PER_MWH + a.values[e.REM], exact)


class MonitorTests(unittest.TestCase):
    def test_unconfigured_never_invents_watts_or_energy(self):
        m, c, _, _ = monitor_fixture(False); c.ms=60000; m.poll()
        d=m.snapshot()
        self.assertIsNone(d["power_mw_est"]); self.assertIsNone(d["energy_mwh_est"])
        self.assertEqual(d["quality"], "unconfigured")
        self.assertEqual(d["unmodeled_s"], 60)

    def test_mode_change_excludes_unknown_interval(self):
        m,c,s,b = monitor_fixture(); c.ms=1000; m.poll()
        b.DISPLAY.brightness=.1; c.ms=2000; m.poll()
        self.assertEqual(m.counter.values[e.UNKNOWN_S], 1)
        self.assertIsNone(m.counter.power_mw)

    def test_busy_duration_not_double_counted(self):
        m,c,_,_ = monitor_fixture(); c.ms=50000; m.transition("mqtt")
        c.ms=60000; m.transition("idle")
        self.assertEqual(m.snapshot()["power_mw_est"], 466)
        self.assertEqual(m.counter.values[e.E]*e.UJ_PER_MWH + m.counter.values[e.REM], 28000000)

    def test_http_activity_not_silently_idle(self):
        m,c,s,_ = monitor_fixture(); s.last_http_ts=10; c.ms=1000; m.poll()
        self.assertEqual(m.counter.values[e.UNKNOWN_S], 1)

    def test_missing_sensor_is_unmodeled(self):
        m,c,s,_ = monitor_fixture(); s.sensor=None; c.ms=1000; m.poll()
        self.assertIsNone(m.counter.power_mw)

    def test_profile_mismatch_rejected(self):
        for field in ("board_id", "sensor_model", "firmware", "circuitpython", "build_id", "boundary"):
            c,s,b,v,p=fixture(); p[field]="wrong"
            with self.subTest(field=field), self.assertRaises(ValueError):
                Monitor(s,b,v,p,c.ticks,c)

    def test_charging_or_battery_assumption_not_accepted(self):
        c,s,b,v,p=fixture(); p["battery_absent_confirmed"]=False
        with self.assertRaises(ValueError): Monitor(s,b,v,p,c.ticks,c)

    def test_oversized_and_malformed_profiles_rejected(self):
        for edit in (lambda p: p.update(id="x"*97), lambda p: p.update(modes={}),
                     lambda p: p.update(modes={(1,):400}),
                     lambda p: p.update(modes={next(iter(p["modes"])): True})):
            c,s,b,v,p=fixture(); edit(p)
            with self.assertRaises(ValueError): Monitor(s,b,v,p,c.ticks,c)

    def test_no_ntp_no_reset_timestamp(self):
        m,c,_,_ = monitor_fixture(); c.ms=1000; m.poll()
        self.assertIsNone(m.snapshot()["last_reset"])

    def test_reset_timestamp_stable_across_ntp_adjustment(self):
        m,c,s,_ = monitor_fixture(); c.ms=60000; s.ntp_synced=True; m.poll()
        initial=m.last_reset
        self.assertTrue(initial.endswith("Z"))
        c.time=lambda: 1999999999; c.ms=61000; m.poll()
        self.assertEqual(m.last_reset, initial)

    def test_periodic_reads_are_throttled(self):
        m,c,_,_ = monitor_fixture(); before=m.counter.last_tick
        c.ms=999; m.poll(); self.assertEqual(m.counter.last_tick,before)
        c.ms=1000; m.poll(); self.assertEqual(m.counter.last_tick,1000)

    def test_snapshot_cached_and_small(self):
        m,c,_,_=monitor_fixture(); c.ms=60000
        before=list(m.counter.values); d=m.snapshot()
        self.assertEqual(list(m.counter.values),before)
        self.assertLess(len(json.dumps(d)),1024)

    def test_physical_sensor_read_is_never_called(self):
        m,c,s,_=monitor_fixture()
        class Sensor:
            def read(self): raise AssertionError("extra CO2 read")
        s.sensor=Sensor()
        c.ms=60000; m.poll(); m.snapshot()


class HookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree=ast.parse((ROOT/'code.py').read_text())
        funcs=[n for n in tree.body if isinstance(n,ast.FunctionDef)
               and n.name in ('_energy_observe','_busy')]
        cls.code=compile(ast.Module(body=funcs,type_ignores=[]),'actual-hooks','exec')

    def run_hook(self, monitor, fails=False):
        calls=[]
        state=types.SimpleNamespace(energy_monitor=monitor)
        runtime=types.SimpleNamespace(show_busy=lambda x:calls.append('show'),clear_busy=lambda:calls.append('clear'))
        buttons=types.SimpleNamespace(scan=lambda:calls.append('buttons'))
        env={'state':state,'runtime':runtime,'buttons':buttons,'log':lambda *a,**k:None}
        exec(self.code,env)
        def work():
            calls.append('co2-work')
            if fails: raise ValueError('original failure')
            return 42
        if fails:
            with self.assertRaisesRegex(ValueError,'original failure'): env['_busy']('cloud',work)
        else: self.assertEqual(env['_busy']('cloud',work),42)
        self.assertEqual(calls,['show','co2-work','clear','buttons'])
        return state

    def test_disabled_does_not_interfere(self): self.run_hook(None)
    def test_observer_failure_does_not_suppress_product_work(self):
        def bad(*a,**k): raise MemoryError('injected')
        state=self.run_hook(types.SimpleNamespace(transition=bad))
        self.assertIsNone(state.energy_monitor)
    def test_underlying_exception_is_not_swallowed(self): self.run_hook(None,True)
    def test_busy_finally_restores_idle(self):
        events=[]; self.run_hook(types.SimpleNamespace(transition=events.append),True)
        self.assertEqual(events,['cloud','idle'])
    def test_periodic_fault_disables_observer(self):
        def bad(): raise RuntimeError('injected')
        state=types.SimpleNamespace(energy_monitor=types.SimpleNamespace(poll=bad))
        env={'state':state,'log':lambda *a,**k:None}; exec(self.code,env)
        env['_energy_observe'](); self.assertIsNone(state.energy_monitor)
    def test_feature_has_no_io_or_dynamic_history(self):
        for path in ('knowco2/energy.py','knowco2/energy_monitor.py'):
            tree=ast.parse((ROOT/path).read_text())
            for node in ast.walk(tree):
                if isinstance(node,ast.Call):
                    name=node.func.id if isinstance(node.func,ast.Name) else getattr(node.func,'attr','')
                    self.assertNotIn(name,('open','write','sleep','save_settings','append','extend'))
    def test_default_profile_has_no_coefficients(self):
        from knowco2.power_profile import PROFILE
        self.assertIsNone(PROFILE)


if __name__ == '__main__': unittest.main()
