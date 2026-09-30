"""偽の GigaSt（設計B §7.4）。実機と同じ要求・同じ送受信のコードで動くことを確かめる。"""
import threading
import time
import unittest
from pathlib import Path

import numpy as np

from pygigast.core.devices.gigast import protocol
from pygigast.core.devices.simulator.device import (
    ReplayIndex,
    SimulatorDevice,
    SynthConfig,
    dbm_to_ad,
)
from pygigast.core.types import DeviceDisconnected, InvalidSweep, Mode, SweepDiscarded

from tests.gigast_helpers import adopted

CAPTURE = Path(__file__).resolve().parents[1] / "ref" / "capture" / "2026-09-30"


def sim(**kw) -> SimulatorDevice:
    kw.setdefault("speed", 0.05)
    kw.setdefault("seed", 1)
    return SimulatorDevice(**kw)


class TestSynth(unittest.TestCase):
    def test_noise_floor_and_lo_leak(self):
        d = sim()
        a = adopted(start_hz=-10_000_000, step_hz=200_000, points=500, preroll=None)
        raw = d.measure(a).raw
        self.assertEqual(len(raw), 500)
        i0 = 50  # 0Hz の点
        self.assertGreater(raw[i0], 1900)
        body = np.delete(raw, range(40, 61))
        self.assertTrue(((body >= 90) & (body <= 130)).all())

    def test_signal_level(self):
        d = sim(config=SynthConfig(signals=[(500_000_000, -30.0)]))
        a = adopted(start_hz=495_000_000, step_hz=20_000, points=500, preroll=0)
        raw = d.measure(a).raw  # OFF は b3=1 なので約1dB 低い
        self.assertAlmostEqual(raw.max(), dbm_to_ad(-31.0), delta=2)
        self.assertEqual(int(np.argmax(raw)), 250)

    def test_image_cancel_removes_moving_spur(self):
        d = sim()
        a = adopted(start_hz=900_000_000, step_hz=200_000, points=500, preroll=0, image_cancel=True)
        r = d.measure(a)
        i956 = (956_000_000 - 900_000_000) // 200_000
        i946 = (946_000_000 - 900_000_000) // 200_000
        self.assertGreater(r.raw_b3_0[i956], 1000)
        self.assertGreater(r.raw_b3_1[i946], 1000)
        self.assertLess(r.raw[i956], 200)
        self.assertLess(r.raw[i946], 200)
        np.testing.assert_array_equal(r.raw, np.minimum(r.raw_b3_0, r.raw_b3_1))

    def test_nf_pair(self):
        r = sim().measure(adopted(mode=Mode.NF, preroll=0))
        self.assertGreater(r.raw_hot.mean(), r.raw_cold.mean() + 50)

    def test_timing_follows_estimate(self):
        d = sim(speed=1.0)
        a = adopted(points=500, preroll=0)  # 見積もり 92ms
        t0 = time.monotonic()
        d.measure(a)
        dt = time.monotonic() - t0
        self.assertGreater(dt, 0.08)
        self.assertLess(dt, 0.5)

    def test_shared_validation(self):
        with self.assertRaises(InvalidSweep):
            sim().measure(adopted(start_hz=12_100_000_000, step_hz=100, points=2))


class TestFaults(unittest.TestCase):
    def test_truncated_is_discarded_then_recovers(self):
        d = sim()
        d.inject("truncate")
        with self.assertRaises(SweepDiscarded) as cm:
            d.measure(adopted(preroll=0))
        self.assertEqual(cm.exception.reason, "silence")
        self.assertEqual(len(d.measure(adopted(preroll=0)).raw), 500)

    def test_no_trailer_and_no_response(self):
        d = sim()
        d.inject("no_trailer")
        with self.assertRaises(SweepDiscarded):
            d.measure(adopted(preroll=0))
        d.inject("no_response")
        with self.assertRaises(SweepDiscarded) as cm:
            d.measure(adopted(preroll=0))
        self.assertEqual(cm.exception.reason, "first_byte")
        d.measure(adopted(preroll=0))

    def test_stray_byte_is_flushed(self):
        d = sim(speed=0.2)
        d.inject("stray_byte")
        r1 = d.measure(adopted(preroll=0)).raw
        time.sleep(0.19 * 0.2 + 0.05)  # 余計な 00 が届くのを待つ
        r2 = d.measure(adopted(preroll=0)).raw
        self.assertEqual(len(r2), 500)
        self.assertGreaterEqual(int(r2[0]), 90)  # 先頭に 00 が混ざっていない
        self.assertEqual(len(r1), 500)

    def test_image_cancel_pair_discard(self):
        d = sim()
        d.inject("truncate")
        with self.assertRaises(SweepDiscarded) as cm:
            d.measure(adopted(preroll=0, image_cancel=True))
        self.assertTrue(cm.exception.detail["pair_discarded"])
        self.assertEqual(len(d.port.requests), 1)

    def test_disconnect_and_plug(self):
        d = sim()
        d.inject("disconnect")
        with self.assertRaises(DeviceDisconnected):
            d.measure(adopted(preroll=0))
        with self.assertRaises(DeviceDisconnected):
            d.measure(adopted(preroll=0))
        d.plug()
        self.assertEqual(len(d.measure(adopted(preroll=0)).raw), 500)

    def test_cancel_waits_for_sweep(self):
        d = sim(speed=1.0)
        ev = threading.Event()
        threading.Timer(0.03, ev.set).start()
        t0 = time.monotonic()
        with self.assertRaises(SweepDiscarded) as cm:
            d.measure(adopted(preroll=0, image_cancel=True), ev)
        self.assertEqual(cm.exception.reason, "cancelled")
        self.assertGreater(time.monotonic() - t0, 0.08)  # 1つ目の掃引は最後まで受けた
        self.assertEqual(len(d.port.requests), 1)


@unittest.skipUnless((CAPTURE / "README.md").is_file(), "実機の受信データ（ref/capture）が無い")
class TestReplay(unittest.TestCase):
    def test_replay_matches_request(self):
        idx = ReplayIndex.from_paths([CAPTURE])
        self.assertGreater(len(idx), 100)
        # t12_health_a1_before.bin: Start 489.98MHz・Step 200・Plot 751・Sample 0・MODE 0x02・SG 500MHz
        a = adopted(start_hz=489_980_000, step_hz=20_000, points=751, preroll=0, mode=Mode.SG,
                    sg_hz=500_000_000, image_cancel=True)
        d = sim(replay=idx)
        ds = protocol.device_sweep(a, d.profile(), 0)
        recorded = idx._by_request[protocol.pack(ds)]  # 同じ要求の健全性確認は何回もある
        want = (CAPTURE / "t12_health_a1_before.bin").read_bytes()
        self.assertIn(want, recorded)
        r = d.measure(a)
        self.assertEqual(d.port.last_source, "synth")  # b3=1 側は記録に無いので合成
        self.assertIn(r.raw_b3_0.tobytes(), [x[:751 * 2] for x in recorded])

    def test_falls_back_to_synth(self):
        d = sim(replay=ReplayIndex.from_paths([CAPTURE]))
        d.measure(adopted(start_hz=123_400_000, preroll=0))
        self.assertEqual(d.port.last_source, "synth")


if __name__ == "__main__":
    unittest.main()
