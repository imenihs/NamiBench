"""PlutoSDR のスカラー S11（設計Q §7.8）。libiio の偽物（ブリッジと標準器: fake.py の bridge・set_standard）を使う。実機は使わない。

- プロファイル: S11 は PlutoSDR のプロファイルが許すときだけ（RTL-SDR は測れない）
- 機器の層: S11 は TG と同じ掃引（多トーン・方形の窓・TG 高調波の平均除去・見積もり）。標準器を偽物へ伝える
- 本体: オープン → ショートの手順と、被測定物のリターンロス／オープンの前に送信しながら利得を合わせ、選んだ利得を
  欄（状態）に出して知らせる（自動なら手動へ移す）／合わせられない・やめたらオープンを測らず理由を出す／オープンを
  測り始めてから基準を捨てるか S11 を出るまで、受信の利得と送信の減衰を変えさせない（電源の入れ直しでも外れる）／
  TG の基準は S11 を固定しない（TG の基準の前の利得合わせと固定は tests/test_pluto_tg_ref.py）／TG 高調波の平均除去が S11 にも効く／
  基準の区間は補正の表を覚える（オープンとショートで表が違えば作らない）
"""
from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

import numpy as np

from namibench.api.local import _PlutoSdrBackend, _SimulatorBackend
from namibench.core.devices.base import load_profile
from namibench.core.devices.plutosdr import driver as pd
from namibench.core.devices.plutosdr.fake import FAKE_SERIAL, FAKE_URI, FakeIioLib, FakePluto
from namibench.core.devices.rtlsdr import driver as rtl_driver
from namibench.core.devices.rtlsdr import spectrum
from namibench.core.instrument import GIGAST_PROFILE_PATH, Instrument
from namibench.core.instrument_common import CommandError
from namibench.core.sweep.model import SweepModel
from namibench.core.types import DeviceError, Mode, SweepDiscarded, SweepRequest
from tests.test_ref_gain_table import A, B, table

MHZ = 1_000_000
S11_LOCK = "plutosdr_s11_conditions_locked"
GAMMA = 0.1  # 被測定物の反射（リターンロス 20dB）


def bridge(**kw) -> FakePluto:
    """TX → ブリッジ → RX。損失 10dB・方向性 60dB（被測定物の反射に対する漏れが 0.1%: 比の誤差 ±0.01dB 程度）。"""
    kw.setdefault("signals", [])
    kw.setdefault("loss_db", 10.0)
    kw.setdefault("directivity_db", 60.0)
    kw.setdefault("dut_gamma", lambda f: GAMMA * np.ones_like(f))
    return FakePluto(bridge=True, **kw)


class TestProfile(unittest.TestCase):
    def test_s11_only_where_the_profile_allows(self):
        prof = pd.default_profile()
        self.assertIn("s11", prof["measurements"])
        m = SweepModel(prof, pd.timing_fn(prof))
        a = m.apply(SweepRequest(mode=Mode.S11), m.default())
        self.assertEqual(a.mode, Mode.S11)
        rtl = rtl_driver.default_profile()
        self.assertNotIn("s11", rtl["measurements"])
        mr = SweepModel(rtl, rtl_driver.timing_fn(rtl))
        self.assertNotEqual(mr.apply(SweepRequest(mode=Mode.S11), mr.default()).mode, Mode.S11)


class TestDriver(unittest.TestCase):
    def setUp(self):
        self.pl = bridge(seed=5)
        self.lib = FakeIioLib(self.pl)
        self.dev = pd.PlutoSdrDevice(lib=self.lib)
        self.dev.open(FAKE_URI)
        self.dev.set_tx_atten(-10)
        self.dev.set_gain(20)
        self.prof = self.dev.profile()
        self.count = 1
        self.model = SweepModel(self.prof, pd.timing_fn(self.prof, lambda: self.count))

    def tearDown(self):
        self.dev.close()

    def adopted(self, mode, start=985 * MHZ, stop=1015 * MHZ):
        return self.model.apply(SweepRequest(mode=mode, start_hz=start, stop_hz=stop, rbw_preference_hz=6000,
                                             edited=("start_hz", "stop_hz")), self.model.default())

    def test_s11_sweep_is_the_tg_sweep(self):
        s11, tg = self.adopted(Mode.S11), self.adopted(Mode.TG)
        r = self.dev.measure(s11)
        self.assertEqual(r.extra["window"], spectrum.RECT)  # 多トーンは方形の窓で読む
        self.assertEqual(r.extra["tx"], "tg")  # 送信は TG の多トーン
        self.assertIsNotNone(self.pl.tx_buffer)
        rt = self.dev.measure(tg)
        ok = np.isfinite(r.raw) & np.isfinite(rt.raw)
        diff = 10 * np.log10(r.raw[ok]) - 10 * np.log10(rt.raw[ok])
        self.assertLess(float(np.median(np.abs(diff))), 0.05)
        self.assertEqual(pd.estimate_s(self.prof, s11.axis, s11.rbw_hz, Mode.S11, 4),
                         pd.estimate_s(self.prof, tg.axis, tg.rbw_hz, Mode.TG, 4))

    def test_harmonic_averaging_applies_in_s11(self):
        self.count = 4
        self.dev.set_tg_harmonic_avg(4)
        a = self.adopted(Mode.S11)
        centers = list(pd.make_plan(self.prof, a.axis, a.rbw_hz)[1].centers)
        self.pl.reads.clear()
        r = self.dev.measure(a)
        reads = [int(x) for x in self.pl.reads]
        for c in centers:
            self.assertEqual(reads.count(c), 4 if c < 1000 * MHZ else 1, c)
        self.assertEqual(r.extra["tg_harmonic_avg"], 4)
        self.assertGreater(pd.estimate_s(self.prof, a.axis, a.rbw_hz, Mode.S11, 4),
                           pd.estimate_s(self.prof, a.axis, a.rbw_hz, Mode.S11, 1))

    def test_fit_gain_sends_the_multitone_in_s11(self):
        n0 = len(self.pl.log)
        res = self.dev.fit_gain(self.adopted(Mode.S11, 400 * MHZ, 402 * MHZ))
        self.assertIn(("cf-ad9361-dds-core-lpc/buffer_out", "push"), [w[:2] for w in self.pl.log[n0:]])
        self.assertFalse(res["overload"])
        self.assertIsNone(self.dev.tx_on)  # 止めていた（掃引していない）ので合わせ終えたら止める

    def test_standard_reaches_the_fake_only(self):
        self.dev.on_reference_standard("open")
        self.assertEqual(self.pl.standard, "open")
        self.dev.on_reference_standard(None)
        self.assertIsNone(self.pl.standard)
        self.assertEqual(self.pl.standards, ["open", None])

        class NoHook:  # 実機の libiio（set_standard を持たない）
            pass

        d = pd.PlutoSdrDevice(lib=NoHook())
        d.on_reference_standard("short")  # 何もしない


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pl = bridge()
        self.lib = FakeIioLib(self.pl)
        pd._cache = pd._SerialCache()
        profile = load_profile(GIGAST_PROFILE_PATH)
        sim = _SimulatorBackend(profile)
        sim.speed = 0.05
        self.inst = Instrument({"simulator": sim, "plutosdr": _PlutoSdrBackend(lib_factory=lambda: self.lib)},
                               profile=profile, data_root=self.root / "data", cal_root=self.root / "cal")
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

    def call(self, name, /, **kw):
        return self.inst.submit(name, **kw).result(30)

    def wait_for(self, pred, timeout=60):
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

    def clear(self):
        with self.cv:
            self.events.clear()

    def notices(self, code=None) -> list[dict]:
        with self.cv:
            return [p for k, p in self.events if k == "notice" and (code is None or p["code"] == code)]

    def task_end(self) -> dict:
        return self.wait_for(lambda k, p: k == "notice" and p["code"] in ("task_done", "task_failed", "task_cancelled")
                             and p["detail"].get("task") == "reference_take")

    def s11(self, mode="s11"):
        self.call("connect", backend="plutosdr", serial=FAKE_SERIAL)
        self.call("set_mode", mode=mode)
        self.call("set_sweep", start_hz=400 * MHZ, stop_hz=402 * MHZ)
        self.call("plutosdr_set", tx_atten_db=-10.0, gain_db=60.0)

    def take(self, standard, expect="task_done") -> dict:
        self.clear()
        # FFT の機器の基準はいまの条件で取る（画面もオープンに kind="fine" を送る: screen_s11.s11_view の range_fixed）
        self.call("reference_take", standard=standard, **({"kind": "fine"} if standard == "open" else {}))
        p = self.task_end()
        self.assertEqual(p["code"], expect, p)
        return p

    def pluto(self) -> dict:
        return self.call("get_state")["plutosdr"]

    def single(self) -> np.ndarray:
        self.clear()
        self.call("run", mode="single")
        self.wait_for(lambda k, p: k == "trace" and p.get("trace") == 1)
        end = time.monotonic() + 30
        while self.call("get_state")["run"]["state"] != "stopped":
            self.assertLess(time.monotonic(), end)
            time.sleep(0.02)
        return np.asarray(self.call("get_trace", id=1)["values"], dtype=float)


class TestFlow(Case):
    def test_open_short_and_return_loss(self):
        self.s11()
        st = self.call("get_state")
        self.assertEqual(st["sweep"]["mode"], "s11")
        p = self.take("open")
        self.assertEqual(p["detail"]["result"]["next"], "short")
        self.assertIsNotNone(self.call("get_state")["normalization"]["s11_open"])
        self.take("short")
        self.assertEqual(self.pl.standards, ["open", None, "short", None])
        n = self.call("get_state")["normalization"]
        self.assertEqual(n["selected"]["name"][:3], "S11")
        v = self.single()
        ok = np.isfinite(v)
        self.assertGreater(int(ok.sum()), 100)
        # 被測定物 Γ = 0.1 → −20dB（GigaSt と同じく S11 の比の dB）
        self.assertAlmostEqual(float(np.median(v[ok])), 20 * np.log10(GAMMA), delta=0.3)
        self.assertEqual(self.call("get_trace", id=1)["unit"], "dB")

    def test_gain_is_fitted_with_the_open_before_measuring_it(self):
        self.s11()
        seen = []
        orig = self.pl.samples

        def rec(n):
            seen.append((self.pl.standard, self.pl.rx_gain()))
            return orig(n)

        self.pl.samples = rec
        p = self.take("open")
        fitted = self.notices("s11_open_gain_fitted")
        self.assertEqual(len(fitted), 1)
        d = fitted[0]["detail"]
        self.assertEqual(d["previous_gain_db"], 60.0)
        self.assertFalse(d["was_auto"])
        self.assertFalse(d["overload"])
        g = d["gain_db"]
        # 知らせはオープンを測り終える前（合わせてから測る）
        order = [q["code"] for q in self.notices()]
        self.assertLess(order.index("s11_open_gain_fitted"), order.index("task_done"))
        self.assertEqual(p["detail"]["phase"], "measure")
        # 合わせている間もオープンを繋いでいる。いくつかの利得を試し、オープンは選んだ利得で読む
        self.assertTrue(all(s == "open" for s, _g in seen))
        tried = {gg for _s, gg in seen}
        self.assertGreater(len(tried), 2)
        self.assertEqual(seen[-1][1], g)
        # 選んだ利得は手動の値として設定に残り、欄（状態）に出る
        pl = self.pluto()
        self.assertEqual(pl["gain_db"], g)
        self.assertFalse(pl["agc"])
        # 振り切れの境目から 3dB 下（偽物: 多トーンの山 0.5・損失 10dB・送信 −10dB → 26dB 付近で振り切れる）
        self.assertTrue(20 <= g <= 24, g)

    def test_auto_gain_moves_to_manual(self):
        self.s11()
        self.call("plutosdr_set", agc=True)
        self.take("open")
        d = self.notices("s11_open_gain_fitted")[0]["detail"]
        self.assertTrue(d["was_auto"])
        pl = self.pluto()
        self.assertFalse(pl["agc"])
        self.assertEqual(pl["gain_db"], d["gain_db"])

    def test_fit_failure_does_not_measure_the_open(self):
        self.s11()

        def boom(*a, **k):
            raise DeviceError("broken")

        self.inst._device.inner.fit_gain = boom
        p = self.take("open", "task_failed")
        self.assertEqual(p["detail"]["error"]["code"], "s11_open_fit_failed")
        st = self.call("get_state")
        self.assertIsNone(st["normalization"]["s11_open"])
        self.assertIsNone(st["plutosdr"]["gain_lock"])
        self.assertIsNone(st["plutosdr"]["tx_atten_lock"])
        self.assertEqual(st["plutosdr"]["gain_db"], 60.0)
        self.assertEqual(self.notices("s11_open_gain_fitted"), [])

    def test_overload_at_the_lowest_gain_does_not_measure_the_open(self):
        self.s11()
        self.pl.loss_db = -40.0  # いちばん小さい利得でも振り切れる強さ
        self.call("plutosdr_set", tx_atten_db=0.0)
        p = self.take("open", "task_failed")
        self.assertEqual(p["detail"]["error"]["code"], "s11_open_overload")
        st = self.call("get_state")
        self.assertIsNone(st["normalization"]["s11_open"])
        self.assertIsNone(st["plutosdr"]["tx_atten_lock"])
        d = self.notices("s11_open_gain_fitted")[0]["detail"]
        self.assertTrue(d["overload"])
        self.assertEqual(st["plutosdr"]["gain_db"], d["gain_db"])  # 選んだ（いちばん小さい）利得は知らせて残す

    def test_cancel_during_the_fit(self):
        self.s11()
        started = threading.Event()

        def slow(sweep, cancel=None, progress=None):
            started.set()
            while not cancel.is_set():
                time.sleep(0.01)
            raise SweepDiscarded("cancelled", {})

        self.inst._device.inner.fit_gain = slow
        self.clear()
        tid = self.call("reference_take", standard="open", kind="fine")["task_id"]
        self.assertTrue(started.wait(10))
        st = self.call("get_state")
        task = next(t for t in st["tasks"] if t["task_id"] == tid)
        self.assertEqual(task["detail"]["phase"], "fit_gain")
        # 合わせている間（オープンを測り始めた時点）から変えさせない
        self.assertEqual(st["plutosdr"]["gain_lock"], S11_LOCK)
        with self.assertRaises(CommandError) as cm:
            self.call("plutosdr_set", tx_atten_db=-20.0)
        self.assertEqual(cm.exception.code, S11_LOCK)
        self.call("cancel_task", task_id=tid)
        p = self.task_end()
        self.assertEqual(p["code"], "task_cancelled")
        st = self.call("get_state")
        self.assertIsNone(st["normalization"]["s11_open"])
        self.assertIsNone(st["plutosdr"]["tx_atten_lock"])
        self.assertEqual(st["plutosdr"]["gain_db"], 60.0)


class TestLocks(Case):
    def assert_locked(self):
        pl = self.pluto()
        self.assertEqual(pl["gain_lock"], S11_LOCK)
        self.assertEqual(pl["tx_atten_lock"], S11_LOCK)
        for kw in ({"gain_db": 40.0}, {"agc": True}, {"tx_atten_db": -20.0}):
            with self.assertRaises(CommandError) as cm:
                self.call("plutosdr_set", **kw)
            self.assertEqual(cm.exception.code, S11_LOCK, kw)
        with self.assertRaises(CommandError):
            self.call("plutosdr_fit_gain")
        self.call("plutosdr_set", tx_atten_db=-10.0)  # 同じ値は断らない（変えていない）

    def assert_free(self):
        pl = self.pluto()
        self.assertIsNone(pl["gain_lock"])
        self.assertIsNone(pl["tx_atten_lock"])
        self.call("plutosdr_set", tx_atten_db=-11.0)
        self.call("plutosdr_set", tx_atten_db=-10.0)

    def test_from_open_until_the_reference_is_discarded(self):
        self.s11()
        self.assert_free()
        self.take("open")
        self.assert_locked()
        self.take("short")
        self.assert_locked()
        g = self.pluto()["gain_db"]
        # 基準を使ったままオープンを測り直しても、利得は合わせ直さない（決まっている利得のまま）
        self.clear()
        self.take("open")
        self.assertEqual(self.notices("s11_open_gain_fitted"), [])
        self.assertEqual(self.pluto()["gain_db"], g)
        self.take("short")
        self.call("reference_select", id=None)
        self.assert_free()

    def test_leaving_s11_discards_the_open_and_frees(self):
        self.s11()
        self.take("open")
        self.assert_locked()
        self.clear()
        self.call("set_mode", mode="sa")
        self.assertEqual(self.notices("s11_open_discarded")[0]["detail"]["reason"], "mode")
        self.assertIsNone(self.call("get_state")["normalization"]["s11_open"])
        self.assert_free()
        self.call("set_mode", mode="s11")
        self.assert_free()

    def test_power_cycle_discards_the_open_and_frees(self):
        self.s11()
        self.take("open")
        self.assert_locked()
        self.call("disconnect")
        self.clear()
        self.call("connect", backend="plutosdr", serial=FAKE_SERIAL)
        self.assertEqual(self.notices("s11_open_discarded")[0]["detail"]["reason"], "power_cycled")
        self.assert_free()

    def test_tg_reference_does_not_lock_s11(self):
        """TG の基準を選んだまま S11 にしても、S11 の基準は無いので固定しない。オープンの前に利得を合わせる。"""
        self.s11(mode="tg")
        self.call("reference_take", kind="fine")
        self.assertEqual(self.task_end()["code"], "task_done")
        self.assertIsNotNone(self.call("get_state")["normalization"]["selected"])
        self.call("set_mode", mode="s11")
        self.assert_free()
        self.clear()
        self.take("open")
        self.assertEqual(len(self.notices("s11_open_gain_fitted")), 1)
        self.assert_locked()

    def test_gigast_s11_has_no_lock_and_no_fit(self):
        self.call("connect", backend="simulator", serial="SIM00001")
        self.call("set_mode", mode="s11")
        self.clear()
        self.call("reference_take", standard="open")
        self.assertEqual(self.task_end()["code"], "task_done")
        self.assertEqual(self.notices("s11_open_gain_fitted"), [])
        self.assertIsNone(self.call("get_state")["plutosdr"]["gain_lock"])


class TestHarmonicAndTable(Case):
    def test_harmonic_averaging_in_s11(self):
        self.s11()
        st = self.pluto()
        self.assertIsNotNone(st["tg_harmonic_estimates"])  # S11 でも選べて見積もりが出る
        self.call("set_sweep", start_hz=990 * MHZ, stop_hz=992 * MHZ)
        self.call("plutosdr_set", tg_harmonic_avg=4)
        self.single()
        self.assertEqual(self.inst._last_raw.extra["tg_harmonic_avg"], 4)
        self.assertEqual(self.pluto()["tg_harmonic_used"], 4)

    def test_reference_remembers_the_gain_table(self):
        self.s11()
        self.inst._pluto_gain_cal = table(A)
        self.take("open")
        self.take("short")
        self.assertEqual(self.inst._ref.segments[-1].gain_table, A)

    def test_open_and_short_with_different_tables_build_nothing(self):
        self.s11()
        self.inst._pluto_gain_cal = table(A)
        self.take("open")
        self.inst._pluto_gain_cal = table(B, 1.0)
        p = self.take("short", "task_failed")
        self.assertEqual(p["detail"]["error"]["code"], "reference_build_failed")
        self.assertIsNone(self.inst._ref)


if __name__ == "__main__":
    unittest.main()
