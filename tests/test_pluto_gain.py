"""PlutoSDR の受信の利得の補正（設計Q §7.6）の試験。libiio の偽物で、実測の形の利得の段差をまねて確かめる。

- 表の計算（基準の利得・列・周波数と利得の補間・端の値・保存と読み込み・途中の保存は前の表の列のまま）
- 機器の口の手順（段差を補正で消せる・送信は固定の順で始め、終わり・やめた・失敗のどれでも止まる・利得が戻る）
- 本体（校正の画面の作業として測る・保存する・表示のレベルに当てる・TG で基準を選んでいる間は利得を変えさせない）
"""
from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

import numpy as np

from pygigast.api.local import _PlutoSdrBackend, _SimulatorBackend
from pygigast.core.devices.base import load_profile
from pygigast.core.devices.plutosdr import driver as pd
from pygigast.core.devices.plutosdr import gain_cal
from pygigast.core.devices.plutosdr.fake import FAKE_SERIAL, FAKE_URI, FakeIioLib, FakePluto, measured_like_gain_error
from pygigast.core.instrument import GIGAST_PROFILE_PATH, Instrument
from pygigast.core.instrument_common import CommandError
from pygigast.core.level import pluto_gain as pg
from pygigast.core.types import DeviceDisconnected, SweepDiscarded
from tests.test_plutosdr import _output_on_wrong

MHZ = 1_000_000
# 試験で測る周波数（3 つの帯から1つずつ。全部の 120 点は試験には長い）
FREQS = [100 * MHZ, 3500 * MHZ, 5000 * MHZ]
TX = {"pd": ("ad9361-phy", "altvoltage1", True, "powerdown"), "att": ("ad9361-phy", "voltage0", True, "hardwaregain"),
      "scale": ("cf-ad9361-dds-core-lpc", "altvoltage0", True, "scale")}


def pluto(**kw) -> FakePluto:
    """TX → 減衰器 40dB → RX の直結。実測の形の利得の段差と、利得に依らない A/D の雑音（実機の低い利得の雑音の高さ）。"""
    kw.setdefault("signals", [])
    kw.setdefault("loss_db", 40.0)
    kw.setdefault("gain_error", measured_like_gain_error)
    kw.setdefault("adc_noise_dbfs", -66.0)
    return FakePluto(**kw)


def tx_stopped(pl: FakePluto) -> bool:
    return (pl.attrs[TX["pd"]] == "1" and float(pl.attrs[TX["att"]].split()[0]) == -89.75
            and float(pl.attrs[TX["scale"]]) == 0.0 and pl.tx_buffer is None)


class TestTable(unittest.TestCase):
    def test_grid_and_reference_gain(self):
        g = pg.grid_freqs()
        self.assertEqual(g[0], 70 * MHZ)
        self.assertEqual(g[1], 100 * MHZ)
        self.assertEqual(g[-1], 6000 * MHZ)
        self.assertEqual(len(g), 120)
        self.assertTrue(all(b - a == 50 * MHZ for a, b in zip(g[1:], g[2:])))
        self.assertEqual(pg.reference_gain(-1, 73), 30)
        self.assertEqual(pg.reference_gain(-10, 62), 30)
        self.assertEqual(pg.reference_gain(35, 70), 52)  # 30 が帯の外なら帯の中央

    def test_row_and_interpolation(self):
        lv = {g: -80.0 + (-4.0 if g >= 53 else 0.0) for g in range(-1, 74)}
        r = pg.row_from_levels(100 * MHZ, lv, -1, 73)
        self.assertEqual(r.ref_gain_db, 30)
        self.assertEqual(r.at(30), 0.0)
        self.assertEqual(r.at(60), 4.0)  # 読みが 4dB 低い利得は +4dB を足す
        self.assertEqual(r.at(100), 4.0)  # 列の外は端の値
        # 基準の利得が測れていなければ、測れた利得のうち基準にいちばん近いもの
        r2 = pg.row_from_levels(100 * MHZ, {g: v for g, v in lv.items() if g != 30}, -1, 73)
        self.assertEqual(r2.ref_gain_db, 29)
        self.assertIsNone(pg.row_from_levels(100 * MHZ, {}, -1, 73))
        rows = (pg.GainRow(100 * MHZ, 30, (0, 60), (0.0, 2.0)), pg.GainRow(200 * MHZ, 30, (0, 60), (0.0, 4.0)))
        cal = pg.GainCorrection("m", "S1", "2026-10-12T00:00:00+09:00", {"kind": "calibration_work"}, rows)
        v = cal.values_at([50 * MHZ, 100 * MHZ, 150 * MHZ, 200 * MHZ, 300 * MHZ], 60)
        self.assertEqual(list(np.round(v, 6)), [2.0, 2.0, 3.0, 4.0, 4.0])  # 周波数の間は線形・範囲の外は端の値
        self.assertEqual(list(cal.values_at([150 * MHZ], 30)), [1.5])  # 利得の間も線形

    def test_store_roundtrip_and_merge(self):
        with tempfile.TemporaryDirectory() as d:
            st = pg.GainCorrectionStore(Path(d))
            self.assertIsNone(st.load_current("S1"))
            rows = (pg.GainRow(100 * MHZ, 30, (29, 30, 31), (0.1, 0.0, -0.2), (-40.0,)),)
            cal = pg.GainCorrection("plutosdr-ad9364", "S1", "2026-10-12T10:00:00+09:00", {"kind": "calibration_work"},
                                    rows, {"kind": "pluto_gain"})
            p = st.save(cal)
            self.assertEqual(p.parent.name, "plutosdr")
            back = st.load_current("S1")
            self.assertEqual(back.rows, rows)
            self.assertEqual(back.serial, "S1")
            # 途中で保存した作業は、測っていない周波数を前の表の列のまま持つ
            new = pg.merged_rows(back, [pg.GainRow(200 * MHZ, 30, (30,), (0.0,))])
            self.assertEqual([r.freq_hz for r in new], [100 * MHZ, 200 * MHZ])
            self.assertEqual(new[0], rows[0])


class TestDeviceProcedure(unittest.TestCase):
    def open(self, pl: FakePluto) -> pd.PlutoSdrDevice:
        dev = pd.PlutoSdrDevice(lib=FakeIioLib(pl))
        dev.open(FAKE_URI, gain_db=30.0)
        self.addCleanup(dev.close)
        return dev

    def test_correction_removes_gain_step(self):
        """実測の形の段差（100MHz で 53dB 以上 −4dB・3.5GHz で 55dB 以上 +2dB）を、補正が消す。"""
        pl = pluto()
        dev = self.open(pl)
        res = dev.gain_calibration(FREQS, 40.0)
        self.assertEqual([r.freq_hz for r in res], FREQS)
        for r in res:
            row = pg.row_from_levels(r.freq_hz, r.levels, r.gain_lo_db, r.gain_hi_db)
            self.assertEqual(len(row.gains_db), r.gain_hi_db - r.gain_lo_db + 1)  # 帯の範囲いっぱい・1dB 刻み
            for g in row.gains_db:
                err = measured_like_gain_error(r.rx_lo_hz, g) - measured_like_gain_error(r.rx_lo_hz, row.ref_gain_db)
                # 補正を足した表示の値は、利得を変えても基準の利得の値から 0.5dB 以内
                self.assertLess(abs(err + row.at(g)), 0.5, (r.freq_hz, g, err, row.at(g)))
        self.assertTrue(tx_stopped(pl))
        self.assertIsNone(dev.tx_on)
        self.assertEqual(dev.gain_db, 30.0)  # 測る前の利得へ戻る
        self.assertEqual(float(pl.attrs[("ad9361-phy", "voltage0", False, "hardwaregain")].split()[0]), 30.0)

    def test_tx_starts_in_the_safe_order(self):
        """送信は固定の順（減衰を最小 → LO を止める → 波形を消す → LO → 波形 → LO を動かす → 減衰）で始める:
        出ているのに減衰が最小より大きく、LO が目的の周波数でない瞬間が無い。"""
        pl = pluto()
        dev = self.open(pl)
        start = {"powerdown": pl.attrs[TX["pd"]], "atten": float(pl.attrs[TX["att"]].split()[0]),
                 "lo": int(pl.attrs[("ad9361-phy", "altvoltage1", True, "frequency")]),
                 "scale": float(pl.attrs[TX["scale"]])}
        n0 = len(pl.log)
        dev.gain_calibration([100 * MHZ], 40.0)
        log = pl.log[n0:]
        lo = 100 * MHZ - 1_000_000  # SG の送信の LO（DDS のずらし 1MHz）
        self.assertIsNone(_output_on_wrong(log, start, lo))

    def test_low_snr_raises_tx_and_bridges(self):
        """利得に依らない雑音が高く、1つの送信の減衰では全部の利得で雑音から 20dB 離れないとき: 送信を強めた区間の
        差を測ってそろえるので、段差の補正は変わらない。"""
        pl = pluto(adc_noise_dbfs=-45.0)
        dev = self.open(pl)
        r = dev.gain_calibration([100 * MHZ], 40.0)[0]
        self.assertGreater(len(r.tx_atten_db), 1)  # 送信を強めた
        row = pg.row_from_levels(r.freq_hz, r.levels, r.gain_lo_db, r.gain_hi_db)
        for g in row.gains_db:
            err = measured_like_gain_error(r.rx_lo_hz, g)
            self.assertLess(abs(err + row.at(g)), 0.6, (g, row.at(g)))
        self.assertTrue(tx_stopped(pl))

    def test_cancel_stops_tx(self):
        pl = pluto()
        dev = self.open(pl)
        cancel = threading.Event()
        seen = []

        def progress(done, total):
            seen.append(done)
            if done >= 20:
                cancel.set()

        with self.assertRaises(SweepDiscarded):
            dev.gain_calibration(FREQS, 40.0, cancel, progress)
        self.assertTrue(tx_stopped(pl))
        self.assertLess(max(seen), 30)
        self.assertEqual(dev.gain_db, 30.0)

    def test_no_signal_stops_tx(self):
        """つないでいない（トーンが見えない）: 送信をいちばん強くしても見えなければ失敗にし、送信を止める。"""
        pl = pluto(loss_db=200.0)
        dev = self.open(pl)
        with self.assertRaises(gain_cal.NoSignal):
            dev.gain_calibration([100 * MHZ], 40.0)
        self.assertTrue(tx_stopped(pl))

    def test_overload_even_at_weakest_tx(self):
        """外から強い信号が入っている（送信をいちばん弱くしても振り切れる）: 失敗にし、送信を止める。"""
        pl = pluto(signals=[(100_200_000, -40.0)])
        dev = self.open(pl)
        with self.assertRaises(gain_cal.TooStrong):
            dev.gain_calibration([100 * MHZ], 40.0)
        self.assertTrue(tx_stopped(pl))

    def test_unplug_midway(self):
        pl = pluto()
        dev = self.open(pl)

        def progress(done, total):
            if done == 10:
                pl.unplugged = True

        with self.assertRaises(DeviceDisconnected):
            dev.gain_calibration(FREQS, 40.0, None, progress)
        self.assertFalse(dev.is_open)

    def test_idle_while_calibrating_does_not_wait(self):
        """測っている間に機器スレッドが on_idle を呼んでも待たない（条件の錠を持ったまま待つと制御スレッドが止まる）。"""
        pl = pluto()
        dev = self.open(pl)
        started = threading.Event()

        def progress(done, total):
            if done == 1:
                started.set()
                time.sleep(0.3)

        th = threading.Thread(target=lambda: dev.gain_calibration([100 * MHZ], 40.0, None, progress))
        th.start()
        self.assertTrue(started.wait(10))
        t0 = time.monotonic()
        dev.on_idle()
        self.assertLess(time.monotonic() - t0, 0.1)
        th.join(30)
        self.assertTrue(tx_stopped(pl))


class InstCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # 外の信号は弱く（校正の作業は TX → 減衰器 → RX で、外の信号は入れない。SA の段差を見る用）
        self.pl = pluto(signals=[(100_200_000, -100.0)])
        self.lib = FakeIioLib(self.pl)
        pd._cache = pd._SerialCache()
        profile = load_profile(GIGAST_PROFILE_PATH)
        sim = _SimulatorBackend(profile)
        sim.speed = 0.05
        self.inst = Instrument({"simulator": sim, "plutosdr": _PlutoSdrBackend(lib_factory=lambda: self.lib)},
                               profile=profile, data_root=self.root / "data", cal_root=self.root / "cal")
        self.inst._pluto_gain_grid = [100 * MHZ, 3500 * MHZ]
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

    def task_end(self, task: str) -> dict:
        return self.wait_for(lambda k, p: k == "notice" and p["code"] in ("task_done", "task_failed", "task_cancelled")
                             and p["detail"].get("task") == task)

    def tone_level(self, gain: float) -> float:
        self.call("plutosdr_set", gain_db=gain)
        with self.cv:
            self.events.clear()
        self.call("run", mode="single")
        self.wait_for(lambda k, p: k == "state" and p["run"]["state"] == "stopped"
                      and p["run"]["last_stop_reason"] == "single_done")
        tr = self.call("get_trace", id=1)
        return float(np.nanmax(np.asarray(tr["values"], dtype=float)))

    def connect(self):
        self.call("connect", backend="plutosdr", serial=FAKE_SERIAL)
        self.call("set_sweep", center_hz=100_000_000, span_hz=1_000_000)


class TestInstrumentWork(InstCase):
    def test_measure_save_and_apply(self):
        self.connect()
        st = self.call("get_state")["plutosdr"]
        self.assertIsNone(st["gain_correction"])
        before = self.tone_level(60.0) - self.tone_level(40.0)
        self.assertAlmostEqual(before, -4.0, delta=0.5)  # 補正なし: 段差がそのまま見える
        out = self.call("cal_open", kind="pluto_gain")
        wk = out["calibration_work"]
        self.assertEqual(wk["kind"], "pluto_gain")
        self.assertEqual(wk["serial"], FAKE_SERIAL)
        self.assertIsNone(wk["device_reason"])
        self.assertEqual(wk["inputs"]["ext_atten_db"], 40.0)
        with self.assertRaises(CommandError):
            self.call("cal_set", ext_atten_db=5.0)
        self.call("cal_set", ext_atten_db=30.0)
        self.call("cal_measure")
        with self.assertRaises(CommandError) as cm:
            self.call("plutosdr_fit_gain")
        self.assertEqual(cm.exception.code, "calibration_in_progress")
        p = self.task_end("cal_pluto_gain")
        self.assertEqual(p["code"], "task_done", p)
        wk = self.call("get_state")["calibration_work"]
        rows = wk["pluto"]["rows"]
        self.assertEqual([r["status"] for r in rows], ["measured", "measured"])
        self.assertAlmostEqual(rows[0]["max_db"], 4.0, delta=0.4)
        self.assertEqual(wk["unsaved"], 2)
        self.assertTrue(tx_stopped(self.pl))
        self.call("cal_save")
        st = self.call("get_state")
        self.assertEqual(st["plutosdr"]["gain_correction"]["freqs"], 2)
        self.assertEqual(st["calibration_work"]["unsaved"], 0)
        after = self.tone_level(60.0) - self.tone_level(40.0)
        self.assertLess(abs(after), 0.5)  # 補正の後: 利得を変えても値が変わらない
        self.assertEqual(self.call("get_trace", id=1)["unit"], "dB（未校正）")
        # 保存した表は個体の下に残り、つなぎ直しても読む
        saved = pg.GainCorrectionStore(self.root / "cal").load_current(FAKE_SERIAL)
        self.assertEqual(len(saved.rows), 2)
        self.assertEqual(saved.work["ext_atten_db"], 30.0)

    def test_cancel_keeps_rows_and_stops_tx(self):
        self.connect()
        self.inst._pluto_gain_grid = [100 * MHZ, 3500 * MHZ, 5000 * MHZ, 5500 * MHZ]
        self.call("cal_open", kind="pluto_gain")
        tid = self.call("cal_measure")["task_id"]
        self.wait_for(lambda k, p: k == "state" and (p.get("calibration_work") or {}).get("unsaved", 0) >= 1)
        self.call("cancel_task", task_id=tid)
        p = self.task_end("cal_pluto_gain")
        self.assertEqual(p["code"], "task_cancelled")
        wk = self.call("get_state")["calibration_work"]
        self.assertGreaterEqual(wk["unsaved"], 1)
        self.assertEqual(wk["message"]["code"], "cancelled")
        self.assertIsNone(wk["task_id"])
        end = time.monotonic() + 10
        while not tx_stopped(self.pl):
            self.assertLess(time.monotonic(), end)
            time.sleep(0.02)
        # 測った列だけ保存できる（測っていない周波数は前の表のまま。前が無ければ表に無い）
        self.call("cal_save")
        saved = pg.GainCorrectionStore(self.root / "cal").load_current(FAKE_SERIAL)
        self.assertEqual(len(saved.rows), wk["unsaved"])
        # 掃引は測る前の動作へ戻る
        self.assertEqual(self.call("get_state")["run"]["state"], "stopped")

    def test_not_for_other_device(self):
        self.call("connect", backend="simulator", serial="SIM00001")
        wk = self.call("cal_open", kind="pluto_gain")["calibration_work"]
        self.assertEqual(wk["device_reason"], "calibration_not_for_device")
        with self.assertRaises(CommandError) as cm:
            self.call("cal_measure")
        self.assertEqual(cm.exception.code, "calibration_not_for_device")

    def test_gain_locked_while_tg_reference(self):
        self.connect()
        self.call("set_mode", mode="tg")
        self.call("plutosdr_set", tx_atten_db=-10.0)
        self.assertIsNone(self.call("get_state")["plutosdr"]["gain_lock"])
        self.call("reference_take", kind="fine")
        self.task_end("reference_take")
        st = self.call("get_state")["plutosdr"]
        self.assertEqual(st["gain_lock"], "plutosdr_gain_reference_in_use")
        for kw in ({"gain_db": 50.0}, {"agc": True}):
            with self.assertRaises(CommandError) as cm:
                self.call("plutosdr_set", **kw)
            self.assertEqual(cm.exception.code, "plutosdr_gain_reference_in_use")
        with self.assertRaises(CommandError):
            self.call("plutosdr_fit_gain")
        self.call("plutosdr_set", tx_atten_db=-12.0)  # 送信の減衰は変えられる
        self.call("set_mode", mode="sa")
        self.assertIsNone(self.call("get_state")["plutosdr"]["gain_lock"])
        self.call("plutosdr_set", gain_db=50.0)


if __name__ == "__main__":
    unittest.main()


class TestRecordKeepsCorrection(InstCase):
    def test_playback_levels_include_the_correction(self):
        """記録に当てた補正を残し、再生の換算でも同じ値になる（設計Q §7.6。表は記録に入らない）。"""
        import types

        from pygigast.core.devices.rtlsdr import levels as rtl_levels
        from pygigast.core.record_frames import SegmentLevels
        from pygigast.core.storage.h5file import H5Record

        self.connect()
        # Stop が Step の整数倍に乗らない範囲（測る点は Stop の先まで1点多く、保存は Stop まで）
        self.call("set_sweep", start_hz=100_000_000, stop_hz=101_000_123)
        self.call("plutosdr_set", gain_db=40.0)
        self.inst._pluto_gain_cal = types.SimpleNamespace(
            values_at=lambda f, g: np.full(np.shape(f), 3.0), freqs_hz=[100 * MHZ], created_at="2026-10-12T00:00:00")
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
            s = h.segments[0]
            self.assertGreaterEqual(len(s.raw_extra["gain_corr_db"]), s.points)
            lv = SegmentLevels(s, h.header)
            raw = h.read(0).raw
            got = lv.db_rows(raw)
            plain, _unit = rtl_levels.to_level(raw, s.raw_extra["gain_db"], None)
            ok = np.isfinite(plain)
            np.testing.assert_allclose((got - plain)[ok], 3.0, atol=1e-3)
            r = h.raw_sweep(0, 0)
            fr = lv.frame(r, 1)
            want, _unit = rtl_levels.to_level(r.raw, s.raw_extra["gain_db"], None)
            got1 = 10 * np.log10(np.asarray(fr.values, dtype=float)) + 30  # to_linear の逆
            ok = np.isfinite(want)
            np.testing.assert_allclose(got1[ok] - want[ok], 3.0, atol=1e-3)
