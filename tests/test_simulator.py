"""偽の GigaSt（設計B §7.4）。実機と同じ要求・同じ送受信のコードで動くことを確かめる。"""
import threading
import time
import unittest
from pathlib import Path

import numpy as np

from namibench.core.devices.gigast import protocol
from namibench.core.devices.simulator.device import (
    ReplayIndex,
    SimulatorDevice,
    SynthConfig,
    dbm_to_ad,
)
from namibench.core.types import DeviceDisconnected, InvalidSweep, Mode, SweepDiscarded

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

    def test_accurate_removes_moving_spur(self):
        d = sim()
        a = adopted(start_hz=900_000_000, step_hz=200_000, points=500, preroll=0, image_reject="accurate")
        r = d.measure(a)
        i956 = (956_000_000 - 900_000_000) // 200_000
        i946 = (946_000_000 - 900_000_000) // 200_000
        # 帯の中（900〜1000MHz）は4回（b3=0・1・0・1: 設計F §7.1）。b3=0 の回は 956MHz、b3=1 の回は 946MHz に
        # スプリアスが出る
        self.assertEqual([s.values.shape for s in r.raw_segments], [(4, 500)])
        passes = r.raw_segments[0].values
        self.assertEqual([w[14] for w in d.port.requests], [0x00, 0x08, 0x00, 0x08])
        for k in (0, 2):
            self.assertGreater(passes[k][i956], 1000)
        for k in (1, 3):
            self.assertGreater(passes[k][i946], 1000)
        self.assertLess(r.raw[i956], 200)
        self.assertLess(r.raw[i946], 200)

    def test_fast_removes_moving_spur(self):
        # スピード: b3=0・1 の2回の小さい方でも、b3 で動くスプリアスは消える（設計F §7.1）
        d = sim()
        a = adopted(start_hz=900_000_000, step_hz=200_000, points=500, preroll=0, image_reject="fast")
        r = d.measure(a)
        self.assertEqual([w[14] for w in d.port.requests], [0x00, 0x08])
        self.assertLess(r.raw[(956_000_000 - 900_000_000) // 200_000], 200)
        self.assertLess(r.raw[(946_000_000 - 900_000_000) // 200_000], 200)

    def test_moving_spur_shift_comes_from_profile(self):
        # b3=1 で動くスプリアスの位置は機種のプロファイルの値（画面の漏れの印と同じ値: 設計L §1.4）
        from namibench.core.devices.gigast.protocol import default_profile

        prof = default_profile()
        self.assertEqual(prof["internal_spurs_hz"]["fixed_b3_shift_hz"], {"956000000": -10000000})
        prof["internal_spurs_hz"]["fixed_b3_shift_hz"] = {"956000000": -20000000}
        d = sim(profile=prof)
        a = adopted(start_hz=900_000_000, step_hz=200_000, points=500, preroll=0, image_reject="off")
        raw = d.measure(a).raw  # なし は b3=1 だけ
        self.assertGreater(raw[(936_000_000 - 900_000_000) // 200_000], 1000)
        self.assertLess(raw[(946_000_000 - 900_000_000) // 200_000], 200)
        self.assertLess(raw[(956_000_000 - 900_000_000) // 200_000], 200)

    def test_accurate_4_removes_bursts(self):
        # 掃引ごとに位置の変わる山（T16）を出しても、帯の中の4回の合わせ方で雑音の床に残らない
        cfg = SynthConfig(bursts_per_sweep=5)
        d = sim(config=cfg, seed=3)
        a = adopted(start_hz=84_200_000, step_hz=2_000, points=501, preroll=0, image_reject="accurate")
        r = d.measure(a)
        top = cfg.noise_ad[1] + cfg.burst_db * 1986 / 80
        self.assertTrue((r.raw_segments[0].values >= top - 1).any())  # 生の値には山がある
        self.assertLessEqual(int(r.raw.max()), cfg.noise_ad[1])
        # 山の無い雑音だけの点は平均か min なので、雑音の範囲の中
        self.assertGreaterEqual(int(r.raw.min()), cfg.noise_ad[0])

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

    def test_accurate_pair_discard(self):
        d = sim()
        d.inject("truncate")
        with self.assertRaises(SweepDiscarded) as cm:
            d.measure(adopted(preroll=0, image_reject="accurate"))
        self.assertTrue(cm.exception.detail["pair_discarded"])
        self.assertEqual(cm.exception.detail["passes"], 4)
        self.assertEqual(len(d.port.requests), 1)

    def test_accurate_4set_discarded_if_last_pass_fails(self):
        # 4回のどれかが捨てられたら、その画面ごと捨てる（設計F §7.2）
        d = sim()
        d.port.faults.extend(["", "", ""])  # 1〜3回目は正常（空の文字列は異常なし）
        d.port.faults.append("truncate")
        with self.assertRaises(SweepDiscarded) as cm:
            d.measure(adopted(preroll=0, image_reject="accurate"))
        self.assertEqual(cm.exception.detail["pass"], "s0_b3_1b")
        self.assertEqual(len(d.port.requests), 4)

    def test_accurate_segments_bursts_only_in_band(self):
        # 帯をまたぐ範囲（1000〜1200MHz）を正確で: 帯の中（〜1100MHz）は4回、外は2回の要求を区間ごとに続けて送り、
        # 帯の中にだけ出る山（T18）は帯の中の4回の合わせ方で消える（設計F §7.2）
        cfg = SynthConfig(bursts_per_sweep=8, burst_bands_hz=((-100_000_000, 1_100_000_000),))
        d = sim(config=cfg, seed=5)
        a = adopted(start_hz=1_000_000_000, step_hz=400_000, points=501, preroll=None, image_reject="accurate")
        r = d.measure(a)
        sent = [protocol.unpack(q) for q in d.port.requests]
        self.assertEqual([s.mode_bits for s in sent], [0x00, 0x08, 0x00, 0x08, 0x00, 0x08])
        self.assertEqual([(s.point0, s.points, s.passes) for s in r.raw_segments], [(0, 250, 4), (250, 251, 2)])
        self.assertEqual(len(r.raw), 501)
        top = cfg.noise_ad[1] + cfg.burst_db * 1986 / 80
        self.assertTrue((r.raw_segments[0].values >= top - 1).any())
        self.assertLessEqual(int(r.raw[:250].max()), cfg.noise_ad[1])

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
            d.measure(adopted(preroll=0, image_reject="accurate"), ev)
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
                    sg_hz=500_000_000, image_reject="accurate")
        d = sim(replay=idx)
        ds = protocol.device_sweep(a, d.profile(), 0)
        recorded = idx._by_request[protocol.pack(ds)]  # 同じ要求の健全性確認は何回もある
        want = (CAPTURE / "t12_health_a1_before.bin").read_bytes()
        self.assertIn(want, recorded)
        r = d.measure(a)
        self.assertEqual(d.port.last_source, "synth")  # b3=1 側は記録に無いので合成
        self.assertIn(r.raw_segments[0].values[0].tobytes(), [x[:751 * 2] for x in recorded])

    def test_falls_back_to_synth(self):
        d = sim(replay=ReplayIndex.from_paths([CAPTURE]))
        d.measure(adopted(start_hz=123_400_000, preroll=0))
        self.assertEqual(d.port.last_source, "synth")


if __name__ == "__main__":
    unittest.main()
