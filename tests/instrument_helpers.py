"""計測器の本体をシミュレータで動かす試験の共通の土台。すべて一時フォルダの中で行う。"""
import tempfile
import threading
import time
import unittest
from pathlib import Path

from pygigast.api.local import _SimulatorBackend
from pygigast.core.devices.base import load_profile
from pygigast.core.instrument import GIGAST_PROFILE_PATH, Instrument

SERIAL = _SimulatorBackend.SERIAL


class InstrumentCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.profile = load_profile(GIGAST_PROFILE_PATH)
        self.inst = self.make_instrument()

    def make_instrument(self):
        sim = _SimulatorBackend(self.profile)
        sim.speed = 0.05  # 見積もりの 1/20 の時間で返す
        inst = Instrument({"simulator": sim}, profile=self.profile,
                          data_root=self.root / "data", cal_root=self.root / "cal")
        self.events = []
        self.cv = threading.Condition()

        def on_event(kind, payload):
            with self.cv:
                self.events.append((kind, payload))
                self.cv.notify_all()

        inst.subscribe(on_event)
        return inst

    def tearDown(self):
        self.inst.shutdown()
        self.tmp.cleanup()

    def call(self, name, /, **kw):
        return self.inst.submit(name, **kw).result(20)

    def clear_events(self):
        with self.cv:
            self.events.clear()

    def wait_for(self, pred, timeout=30):
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

    def wait_task(self, task_id, timeout=60):
        """長い処理の終わりの知らせ（task_done / task_failed / task_cancelled）を待つ。"""
        return self.wait_for(lambda k, p: k == "notice" and p["code"].startswith("task_")
                             and p["detail"].get("task_id") == task_id, timeout)

    def connect(self, start_hz=50_000_000, stop_hz=200_000_000):
        self.call("set_sweep", start_hz=start_hz, stop_hz=stop_hz)
        self.call("connect", backend="simulator", serial=SERIAL)

    def single(self):
        self.clear_events()
        self.call("run", mode="single")
        return self.wait_for(lambda k, p: k == "state" and p["run"]["state"] == "stopped"
                             and p["run"]["last_stop_reason"] == "single_done")
