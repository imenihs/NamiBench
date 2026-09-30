"""計測器の本体をシミュレータで通す（設計A §4.2・§4.3、設計B §7.3）。"""
import tempfile
import threading
import time
import unittest
from pathlib import Path

from pygigast.api.local import _SimulatorBackend
from pygigast.core.devices.base import load_profile
from pygigast.core.instrument import GIGAST_PROFILE_PATH, CommandError, Instrument


class InstrumentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        profile = load_profile(GIGAST_PROFILE_PATH)
        sim = _SimulatorBackend(profile)
        sim.speed = 0.05  # 見積もりの 1/20 の時間で返す
        self.inst = Instrument({"simulator": sim}, profile=profile,
                               data_root=root / "data", cal_root=root / "cal")
        self.events = []
        self.cv = threading.Condition()

        def on_event(kind, payload):
            with self.cv:
                self.events.append((kind, payload))
                self.cv.notify_all()

        self.inst.subscribe(on_event)

    def tearDown(self):
        self.inst.shutdown()
        self.tmp.cleanup()

    def call(self, name, **kw):
        return self.inst.submit(name, **kw).result(10)

    def wait_for(self, pred, timeout=20):
        end = time.monotonic() + timeout
        with self.cv:
            while True:
                for k, p in self.events:
                    if pred(k, p):
                        return p
                left = end - time.monotonic()
                if left <= 0:
                    self.fail("イベントが来ない")
                self.cv.wait(left)

    def test_set_sweep_returns_adopted_and_adjustments(self):
        r = self.call("set_sweep", stop_hz=12_300_000_000)
        self.assertEqual(r["adopted"]["stop_hz"], 12_100_000_000)
        self.assertTrue(any(a["field"] == "stop_hz" for a in r["adjustments"]))
        st = self.call("get_state")
        self.assertEqual(st["sweep"]["stop_hz"], 12_100_000_000)

    def test_run_requires_connection(self):
        with self.assertRaises(CommandError) as cm:
            self.call("run", mode="continuous")
        self.assertEqual(cm.exception.code, "not_connected")

    def test_non_integer_frequency_rejected(self):
        with self.assertRaises(CommandError) as cm:
            self.call("set_sweep", start_hz=1.5e6)
        self.assertEqual(cm.exception.code, "not_integer")

    def test_single_sweep_on_simulator(self):
        self.call("set_sweep", start_hz=100_000_000, stop_hz=200_000_000)
        self.call("connect", backend="simulator", serial="SIM00001")
        self.call("run", mode="single")
        t = self.wait_for(lambda k, p: k == "trace")
        self.assertEqual(t["axis"]["start_hz"], 100_000_000)
        self.assertEqual(len(t["values"]), t["axis"]["points"])
        st = self.wait_for(lambda k, p: k == "state" and p["run"]["state"] == "stopped"
                           and p["run"]["last_stop_reason"] == "single_done")
        self.assertEqual(st["run"]["single_done"], 1)

    def test_single_does_not_start_extra_sweep(self):
        # 単発の後に余計な掃引を始めると、次の操作が1掃引分待たされる（実機 2026-10-01 で発見）
        calls = []
        backend = self.inst._backends["simulator"]
        orig_open = backend.open

        def counting_open(serial, port):
            dev = orig_open(serial, port)
            orig_measure = dev.measure

            def measure(adopted, cancel=None):
                calls.append(adopted.axis.start_hz)
                return orig_measure(adopted, cancel)

            dev.measure = measure
            return dev

        backend.open = counting_open
        self.call("set_sweep", start_hz=100_000_000, stop_hz=200_000_000)
        self.call("connect", backend="simulator", serial="SIM00001")
        self.call("run", mode="single", count=3)
        self.wait_for(lambda k, p: k == "state" and p["run"]["last_stop_reason"] == "single_done")
        time.sleep(0.5)
        self.assertEqual(len(calls), 3)

    def test_unplug_then_resume(self):
        # 切れたら「切断・再接続待ち」、同じシリアル番号が戻ったら直前の条件で再開（設計B §7.3）
        devices = []
        backend = self.inst._backends["simulator"]
        orig_open = backend.open

        def tracking_open(serial, port):
            dev = orig_open(serial, port)
            devices.append(dev)
            return dev

        backend.open = tracking_open
        self.call("connect", backend="simulator", serial="SIM00001")
        self.call("set_sweep", start_hz=100_000_000, stop_hz=200_000_000)
        self.call("run", mode="continuous")
        self.wait_for(lambda k, p: k == "trace")
        devices[0].unplug()
        self.wait_for(lambda k, p: k == "state" and p["connection"]["state"] == "reconnecting")
        with self.cv:
            self.events.clear()
        t = self.wait_for(lambda k, p: k == "trace")
        self.assertEqual(t["axis"]["start_hz"], 100_000_000)
        self.assertEqual(len(devices), 2)
        st = self.call("get_state")
        self.assertEqual((st["connection"]["state"], st["run"]["state"]), ("connected", "continuous"))
        self.call("stop")

    def test_condition_change_discards_old_sweep(self):
        self.call("connect", backend="simulator", serial="SIM00001")
        self.call("set_sweep", start_hz=100_000_000, stop_hz=200_000_000)
        self.call("run", mode="continuous")
        self.wait_for(lambda k, p: k == "trace")
        self.call("set_sweep", start_hz=300_000_000, stop_hz=400_000_000)
        with self.cv:
            self.events.clear()
        # 変更後に届くトレースはすべて新しい条件のもの
        t = self.wait_for(lambda k, p: k == "trace")
        self.assertEqual(t["axis"]["start_hz"], 300_000_000)
        self.call("stop")
        self.wait_for(lambda k, p: k == "state" and p["run"]["state"] == "stopped")


if __name__ == "__main__":
    unittest.main()
