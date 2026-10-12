"""PlutoSDR の TG 高調波の平均除去（設計Q §7.7）。libiio の偽物を使う。実機は使わない。

- 機器の層: 1000MHz より下の位置だけ回数分読む（以上は1回）・読む前ごとに送信と受信の LO を離して戻す（送信は動かす間
  だけ出力をいちばん小さくし、利用者の減衰へ戻す）・電力の平均で掃引ごとの揺れが 1/√回数 に減る（偽物は合わせ直すたびに
  無作為の戻りを足す: fake.relock_ripple）・見積もりが読む回数の分だけ延びる
- 本体: 設定に残り次の起動で戻る・受け付ける値・状態の選択肢と見積もり（TG のときだけ）・回数の違う基準も当てる・
  記録に回数が残り再生で使った回数が分かる・PNG の条件の行
"""
from __future__ import annotations

import time
import unittest

import numpy as np

from pygigast.core import report_layout as rl
from pygigast.core.devices.plutosdr import driver as pd
from pygigast.core.devices.plutosdr.fake import FAKE_SERIAL, FAKE_URI, FakeIioLib, FakePluto
from pygigast.core.instrument_common import CommandError
from pygigast.core.storage.h5file import H5Record
from pygigast.core.sweep.model import SweepModel
from pygigast.core.types import Mode, SweepRequest
from tests.test_plutosdr import PlutoCase

MHZ = 1_000_000
RX_LO = "ad9361-phy/altvoltage0_out"
TX_LO = "ad9361-phy/altvoltage1_out"
TX_ATTEN = "ad9361-phy/voltage0_out"
# 合わせ直しの1回の読みの揺れ 約 0.4dB（実機の 150MHz の 0.45dB に近い大きさ）
RIPPLE = 0.07


class DeviceCase(unittest.TestCase):
    def setUp(self):
        self.pl = FakePluto(signals=[], relock_ripple=RIPPLE, seed=3)
        self.lib = FakeIioLib(self.pl)
        self.dev = pd.PlutoSdrDevice(lib=self.lib)
        self.dev.open(FAKE_URI)
        self.dev.set_tx_atten(-10)
        self.prof = self.dev.profile()
        self.count = 1
        self.model = SweepModel(self.prof, pd.timing_fn(self.prof, lambda: self.count))
        # 1000MHz をまたぐ範囲（下の位置はくり返し、上の位置は1回）
        self.tg = self.model.apply(SweepRequest(mode=Mode.TG, start_hz=985 * MHZ, stop_hz=1015 * MHZ,
                                                rbw_preference_hz=6000, edited=("start_hz", "stop_hz")),
                                   self.model.default())
        self.centers = list(pd.make_plan(self.prof, self.tg.axis, self.tg.rbw_hz)[1].centers)
        self.below = [c for c in self.centers if c < 1000 * MHZ]
        self.above = [c for c in self.centers if c >= 1000 * MHZ]

    def tearDown(self):
        self.dev.close()

    def set_count(self, n):
        self.count = n
        self.dev.set_tg_harmonic_avg(n)

    def sweep_db(self, n: int, sweeps: int = 8) -> np.ndarray:
        self.set_count(n)
        return np.array([10 * np.log10(self.dev.measure(self.tg).raw) for _ in range(sweeps)])


class TestDriver(DeviceCase):
    def test_range_has_positions_on_both_sides(self):
        self.assertGreater(len(self.below), 3)
        self.assertGreater(len(self.above), 3)

    def test_reads_repeat_only_below_1000mhz(self):
        self.set_count(4)
        self.pl.reads.clear()
        r = self.dev.measure(self.tg)
        reads = [int(x) for x in self.pl.reads]
        for c in self.below:
            self.assertEqual(reads.count(c), 4, c)
        for c in self.above:
            self.assertEqual(reads.count(c), 1, c)
        self.assertEqual(r.extra["tg_harmonic_avg"], 4)
        self.assertEqual(r.extra["tg_harmonic_positions"], len(self.below))

    def test_off_reads_once_and_does_not_relock(self):
        self.set_count(1)
        n0 = len(self.pl.log)
        self.pl.reads.clear()
        self.dev.measure(self.tg)
        self.assertEqual(sorted(int(x) for x in self.pl.reads), sorted(self.centers))
        away = {c + 10 * MHZ for c in self.centers}
        self.assertFalse([w for w in self.pl.log[n0:] if w[1] == "frequency" and int(w[2]) in away])

    def test_relock_before_each_read(self):
        self.set_count(4)
        self.dev.measure(self.tg)  # 送信を始める分を先に済ませる
        n0 = len(self.pl.log)
        self.pl.reads.clear()
        self.dev.measure(self.tg)
        log = self.pl.log[n0:]
        for c in self.below:
            away = str(c + 10 * MHZ)
            self.assertEqual(sum(1 for w in log if w[:3] == (TX_LO, "frequency", away)), 4, c)
            self.assertEqual(sum(1 for w in log if w[:3] == (RX_LO, "frequency", away)), 4, c)
        for c in self.above:
            away = str(c + 10 * MHZ)
            self.assertFalse([w for w in log if w[1] == "frequency" and w[2] == away], c)

    def test_relock_keeps_the_tx_safe_order(self):
        # 送信の LO を動かす間は出力をいちばん小さく、戻してから利用者の減衰（出力を上げない・利用者の減衰を変えない）
        self.set_count(4)
        self.dev.measure(self.tg)
        n0 = len(self.pl.log)
        self.dev.measure(self.tg)
        log = self.pl.log[n0:]
        atten = None
        user = -10.0
        for w in log:
            if w[:2] == (TX_ATTEN, "hardwaregain"):
                atten = float(w[2])
                self.assertIn(atten, (-89.75, user))
            elif w[:2] == (TX_LO, "frequency") and int(w[2]) - 10 * MHZ in self.below:
                self.assertEqual(atten, -89.75, w)
        self.assertEqual(atten, user)
        self.assertEqual(self.dev.tx_atten_db, user)

    def test_averaging_reduces_the_sweep_to_sweep_spread(self):
        f = self.tg.axis.freqs()
        lo = f < 999 * MHZ
        hi = f > 1002 * MHZ
        sd = {}
        sd_hi = {}
        for n in (1, 4, 8):
            rows = self.sweep_db(n)
            s = np.nanstd(rows, axis=0)
            sd[n] = float(np.nanmedian(s[lo]))
            sd_hi[n] = float(np.nanmedian(s[hi]))
        self.assertGreater(sd[1], 0.25)  # 平均除去なしでは合わせ直しごとの揺れが見える
        # 1/√回数 で減る（4回 0.5 倍・8回 0.35 倍。8 回の掃引の標準偏差なので幅を持たせる）
        self.assertLess(sd[4] / sd[1], 0.7)
        self.assertGreater(sd[4] / sd[1], 0.35)
        self.assertLess(sd[8] / sd[1], 0.5)
        self.assertLess(sd[8], sd[4])
        # 1000MHz 以上は戻りが無く1回の読みのまま（揺れは雑音だけ）
        for n in (1, 4, 8):
            self.assertLess(sd_hi[n], 0.05)

    def test_mean_level_is_unchanged(self):
        # 電力の平均なので、比の真ん中は回数で動かない（基準と回数が違っても当ててよい: 設計Q §7.7）
        f = self.tg.axis.freqs()
        lo = f < 999 * MHZ
        m1 = float(np.nanmean(self.sweep_db(1, 16)[:, lo]))
        m8 = float(np.nanmean(self.sweep_db(8, 4)[:, lo]))
        self.assertAlmostEqual(m1, m8, delta=0.1)

    def test_sa_and_sg_read_once(self):
        self.set_count(8)
        sa = self.model.apply(SweepRequest(mode=Mode.SA), self.tg)
        self.pl.reads.clear()
        r = self.dev.measure(sa)
        self.assertEqual(len(self.pl.reads), len(self.centers))
        self.assertNotIn("tg_harmonic_avg", r.extra)

    def test_estimate_grows_with_the_extra_reads(self):
        t = self.prof["timing"]
        p, pl = pd.make_plan(self.prof, self.tg.axis, self.tg.rbw_hz)
        read_s = pd.samples_per_position(p, self.prof) / min(float(p.fs), float(t["link_samples_per_s"]))
        relock = float(t["tg_relock_s"])
        base = pd.estimate_s(self.prof, self.tg.axis, self.tg.rbw_hz, Mode.TG, 1)
        self.assertAlmostEqual(base, len(self.centers) * (float(t["retune_settle_s"]) + read_s))
        for n in (4, 8):
            est = pd.estimate_s(self.prof, self.tg.axis, self.tg.rbw_hz, Mode.TG, n)
            self.assertAlmostEqual(est - base, len(self.below) * (n * (relock + read_s) - read_s))
            # 掃引条件モデルの見積もりも同じ（TG のときだけ）
            self.count = n
            self.assertAlmostEqual(self.model.apply(SweepRequest(), self.tg).estimated_sweep_s, est)
            sa = self.model.apply(SweepRequest(mode=Mode.SA), self.tg)
            self.assertAlmostEqual(sa.estimated_sweep_s, pd.estimate_s(self.prof, sa.axis, sa.rbw_hz, Mode.SA, 1))
        # 全部の位置が 1000MHz 以上なら延びない
        up = self.model.apply(SweepRequest(start_hz=1500 * MHZ, stop_hz=1520 * MHZ, edited=("start_hz", "stop_hz")),
                              self.tg)
        self.assertAlmostEqual(pd.estimate_s(self.prof, up.axis, up.rbw_hz, Mode.TG, 8),
                               pd.estimate_s(self.prof, up.axis, up.rbw_hz, Mode.TG, 1))

    def test_cancel_between_repeats(self):
        import threading

        from pygigast.core.types import SweepDiscarded

        self.set_count(8)
        ev = threading.Event()
        orig = self.pl.samples

        def samples(n):
            if len(self.pl.reads) >= 2:
                ev.set()
            return orig(n)

        self.pl.samples = samples
        self.pl.reads.clear()
        with self.assertRaises(SweepDiscarded):
            self.dev.measure(self.tg, ev)
        self.assertLess(len(self.pl.reads), 8)


class InstCase(PlutoCase):
    def setUp(self):
        super().setUp()
        self.lib.pluto.relock_ripple = RIPPLE

    def tg(self):
        self.call("connect", backend="plutosdr", serial=FAKE_SERIAL)
        self.call("set_sweep", start_hz=985 * MHZ, stop_hz=1015 * MHZ)
        self.call("set_mode", mode="tg")
        self.call("plutosdr_set", tx_atten_db=-10.0)

    def task_end(self, task):
        return self.wait_for(lambda k, p: k == "notice" and p["code"] in ("task_done", "task_failed", "task_cancelled")
                             and p["detail"].get("task") == task)

    def norm(self) -> dict:
        with self.cv:
            self.events.clear()
        self.call("run", mode="single")
        self.wait_for(lambda k, p: k == "trace" and p.get("trace") == 1)
        end = time.monotonic() + 30
        while self.call("get_state")["run"]["state"] != "stopped":
            self.assertLess(time.monotonic(), end)
            time.sleep(0.02)
        return self.call("get_state")["normalization"]


class TestInstrument(InstCase):
    def test_default_choices_and_bad_values(self):
        st = self.call("get_state")["plutosdr"]
        self.assertEqual(st["tg_harmonic_avg"], 1)
        self.assertEqual(st["tg_harmonic_choices"], [1, 4, 8])
        self.assertIsNone(st["tg_harmonic_estimates"])  # つないでいない
        for bad in (2, 0, True, "4", 4.0):
            with self.assertRaises(CommandError):
                self.call("plutosdr_set", tg_harmonic_avg=bad)

    def test_estimates_only_in_tg_and_sweep_estimate_follows(self):
        self.call("connect", backend="plutosdr", serial=FAKE_SERIAL)
        self.call("set_sweep", start_hz=985 * MHZ, stop_hz=1015 * MHZ)
        self.assertIsNone(self.call("get_state")["plutosdr"]["tg_harmonic_estimates"])  # SA
        self.call("set_mode", mode="tg")
        st = self.call("get_state")
        est = st["plutosdr"]["tg_harmonic_estimates"]
        self.assertEqual(sorted(est), ["1", "4", "8"])
        self.assertLess(est["1"], est["4"])
        self.assertLess(est["4"], est["8"])
        self.assertAlmostEqual(st["sweep"]["estimated_sweep_s"], est["1"])
        res = self.call("plutosdr_set", tg_harmonic_avg=8)
        self.assertEqual(res["plutosdr"]["tg_harmonic_avg"], 8)
        st = self.call("get_state")
        self.assertAlmostEqual(st["sweep"]["estimated_sweep_s"], est["8"])
        self.assertEqual(self.inst._device.inner.tg_harmonic_avg, 8)
        # 条件を変えても選んだ回数で見積もる
        self.call("set_sweep", start_hz=980 * MHZ, stop_hz=1015 * MHZ)
        st = self.call("get_state")
        self.assertAlmostEqual(st["sweep"]["estimated_sweep_s"], st["plutosdr"]["tg_harmonic_estimates"]["8"])

    def test_saved_and_restored(self):
        self.call("plutosdr_set", tg_harmonic_avg=4)
        self.assertEqual(self.inst._settings.get("plutosdr")["tg_harmonic_avg"], 4)
        self.inst.shutdown()
        from pygigast.api.local import _PlutoSdrBackend, _SimulatorBackend
        from pygigast.core.instrument import Instrument

        sim = _SimulatorBackend(self.profile)
        self.inst = Instrument({"simulator": sim, "plutosdr": _PlutoSdrBackend(lib_factory=lambda: self.lib)},
                               profile=self.profile, data_root=self.root / "data", cal_root=self.root / "cal")
        self.assertEqual(self.call("get_state")["plutosdr"]["tg_harmonic_avg"], 4)
        self.call("connect", backend="plutosdr", serial=FAKE_SERIAL)
        self.assertEqual(self.inst._device.inner.tg_harmonic_avg, 4)

    def test_change_keeps_traces(self):
        self.tg()
        self.norm()
        before = self.call("get_trace", id=1)
        self.call("plutosdr_set", tg_harmonic_avg=4)
        after = self.call("get_trace", id=1)
        np.testing.assert_array_equal(np.asarray(before["values"]), np.asarray(after["values"]))  # 蓄積は捨てない

    def test_reference_with_other_count_still_applied(self):
        self.tg()
        self.call("reference_take", kind="fine")
        p = self.task_end("reference_take")
        self.assertEqual(p["code"], "task_done", p)
        self.assertTrue(self.norm()["applied"])
        self.call("plutosdr_set", tg_harmonic_avg=8)
        n = self.norm()
        self.assertTrue(n["applied"], n)
        self.assertIsNone(n.get("reason"))
        self.assertEqual(self.call("get_state")["plutosdr"]["tg_harmonic_used"], 8)

    def test_record_carries_the_count(self):
        self.tg()
        self.call("plutosdr_set", tg_harmonic_avg=4)
        self.call("record_start")
        self.call("run", mode="continuous")
        self.wait_for(lambda k, p: k == "state" and p["recording"].get("sweeps", 0) >= 2)
        self.call("stop")
        path = self.root / "rec.h5"
        tid = self.call("record_finish", path=str(path))["task_id"]
        n = self.wait_for(lambda k, p: k == "notice" and p["code"].startswith("task_")
                          and p["detail"].get("task_id") == tid)
        self.assertEqual(n["code"], "task_done", n)
        with H5Record(path) as h:
            self.assertEqual({s.raw_extra.get("tg_harmonic_avg") for s in h.segments}, {4})
        # 設定を変えても、再生は記録したときの回数を出す
        self.call("plutosdr_set", tg_harmonic_avg=1)
        opened = self.call("open", paths=[str(path)])
        self.wait_for(lambda k, p: k == "notice" and p["code"].startswith("task_")
                      and p["detail"].get("task_id") == opened["task_id"])
        with self.cv:
            self.events.clear()
        self.call("playback_seek", index=0)
        self.wait_for(lambda k, p: k == "trace" and p.get("trace") == 1)
        st = self.call("get_state")
        self.assertTrue(st["playback"]["active"])
        self.assertEqual(st["plutosdr"]["tg_harmonic_used"], 4)
        self.assertEqual(st["plutosdr"]["tg_harmonic_avg"], 1)


class TestPngCondition(unittest.TestCase):
    def data(self, used):
        st = {"sweep": {"start_hz": 1, "stop_hz": 2, "rbw_hz": 6000, "points": 2, "step_hz": 1, "mode": "tg"},
              "connection": {"sweep_kind": "fft"}, "plutosdr": {"tg_harmonic_used": used}}
        return rl.build_data(st, ref_level=0.0, db_per_div=10.0, unit="dB", mode_name="TG", traces=[], markers=[],
                             warnings=[])

    def test_condition_line(self):
        self.assertEqual(self.data(4)["conditions"]["tg_harmonic_avg"], 4)
        self.assertEqual(rl.tg_harmonic_word(4), "4回")
        self.assertEqual(rl.tg_harmonic_word(1), "なし")
        self.assertIsNone(self.data(None)["conditions"]["tg_harmonic_avg"])


if __name__ == "__main__":
    unittest.main()
