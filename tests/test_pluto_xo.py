"""PlutoSDR の基準の補正（設計Q §7.10）の試験。libiio の偽物（devices/plutosdr/fake.py）を使う。実機は使わない。

- 機器の口: 繋ぐときに設定の ppm から計算した値を書く（送信を止めた後・LO の書ける下限を確かめる前）／0ppm は公称の 40MHz を書く（前の値が残っていてもそろえる）／
  範囲の外は書かずに繋ぐ／属性を持たない機器でも繋げる／書いた後はサンプリング周波数を書き直し、LO は次の測定で書き直す
- 本体: 測っている間・外部 IQ の送信の間は断る／止まっていれば書く／繋いでいなければ覚えて次の接続で書く／機器ごとに別の値／
  保存したデータに残る
"""
from __future__ import annotations

import json
import time
import unittest

from namibench.core.devices.plutosdr import driver as pd
from namibench.core.devices.plutosdr import xo
from namibench.core.devices.plutosdr.fake import FAKE_SERIAL, FAKE_URI, FakeIioLib, FakePluto
from namibench.core.devices.rtlsdr.driver import make_plan
from namibench.core.instrument_common import CommandError
from namibench.core.sweep.model import SweepModel
from namibench.core.storage.h5file import raw_extra_for
from namibench.core.types import InvalidSweep, Mode, SweepRequest
from namibench.ui_qt import texts
from tests.test_instrument_ext_tx import ExtCase
from tests.test_plutosdr import PlutoCase, fake_lib

MHZ = 1_000_000
XO = ("ad9361-phy/", "xo_correction")


def xo_writes(pl) -> list[str]:
    return [w[2] for w in pl.log if w[:2] == XO]


class TestXoPieces(unittest.TestCase):
    def test_hz_and_limits(self):
        self.assertEqual(xo.hz_for(0), 40_000_000)
        self.assertEqual(xo.hz_for(1.5), 40_000_060)
        self.assertEqual(xo.hz_for(-200), 39_992_000)
        # 申告 [39992000 1 40008000] は ±200ppm。端の ppm で書く値が範囲に入る（内側に丸める）
        self.assertEqual(xo.ppm_limits(39_992_000, 40_008_000), (-200.0, 200.0))
        lo, hi = xo.ppm_limits(39_992_001, 40_007_999)
        self.assertGreaterEqual(xo.hz_for(lo), 39_992_001)
        self.assertLessEqual(xo.hz_for(hi), 40_007_999)


class TestDriverXo(unittest.TestCase):
    def open(self, ppm=None, **kw):
        self.lib = fake_lib(**kw)
        self.pl = self.lib.pluto
        self.dev = pd.PlutoSdrDevice(lib=self.lib)
        table = None if ppm is None else {FAKE_SERIAL: ppm}
        self.dev.open(FAKE_URI, xo_ppm_by_serial=table)
        self.model = SweepModel(self.dev.profile(), pd.timing_fn(self.dev.profile()))
        return self.dev

    def tearDown(self):
        if getattr(self, "dev", None) is not None:
            self.dev.close()

    def adopt(self, mode="sa", start=84_600_000, stop=84_800_000):
        return self.model.apply(SweepRequest(start_hz=start, stop_hz=stop, mode=Mode(mode),
                                             edited=("start_hz", "stop_hz")), self.model.default())

    def test_written_on_connect_after_tx_stop_and_before_floor(self):
        self.open(1.5)
        self.assertEqual(xo_writes(self.pl), ["40000060"])
        self.assertEqual(self.pl.xo_hz, 40_000_060)
        log = self.pl.log
        i = log.index((*XO, "40000060"))
        # 送信を止めた（LO を止めた）後に書き、送信 LO の書ける下限を確かめる書き込みはその後（設計Q §7.10）
        stop = log.index(("ad9361-phy/altvoltage1_out", "powerdown", "1"))
        floor = next(k for k, w in enumerate(log) if w[:2] == ("ad9361-phy/altvoltage1_out", "frequency"))
        self.assertLess(stop, i)
        self.assertLess(i, floor)
        st = self.dev.xo_state
        self.assertEqual((st["supported"], st["hz"], st["range_hz"], st["ppm"], st["refused_ppm"]),
                         (True, 40_000_060, [39_992_000, 40_008_000], 1.5, None))

    def test_zero_writes_nominal_even_over_a_value_left_from_before(self):
        # 0ppm は公称の 40MHz を書く。前の接続で書いた値が機器に残っていても（電源は入ったまま）、アプリの値にそろえる
        for ppm in (0.0, None):
            with self.subTest(ppm=ppm):
                lib = fake_lib()
                lib.pluto._set_raw("ad9361-phy", "", False, "xo_correction", "39999284")
                self.lib, self.pl = lib, lib.pluto
                self.dev = pd.PlutoSdrDevice(lib=lib)
                self.dev.open(FAKE_URI, xo_ppm_by_serial=None if ppm is None else {FAKE_SERIAL: ppm})
                self.assertEqual(xo_writes(self.pl), ["40000000"])
                self.assertEqual((self.pl.xo_hz, self.dev.xo_state["hz"], self.dev.xo_state["ppm"]), (40_000_000, 40_000_000, 0.0))
                self.dev.close()

    def test_out_of_range_is_refused_but_connects(self):
        self.open(250.0)
        self.assertTrue(self.dev.is_open)
        self.assertEqual(xo_writes(self.pl), [])
        self.assertEqual(self.dev.xo_state["refused_ppm"], 250.0)
        self.assertEqual(self.pl.xo_hz, 40_000_000)

    def test_device_refusal_is_refused_but_connects(self):
        # 申告の範囲の中でも機器が断った（EINVAL）ら書かずに繋ぐ
        lib = fake_lib()
        lib.pluto.xo_range = (39_998_000, 40_002_000)
        self.lib, self.pl = lib, lib.pluto
        self.dev = pd.PlutoSdrDevice(lib=lib)
        self.dev.open(FAKE_URI, xo_ppm_by_serial={FAKE_SERIAL: 100.0})
        self.assertTrue(self.dev.is_open)
        self.assertEqual(self.dev.xo_state["refused_ppm"], 100.0)
        self.assertEqual(self.pl.xo_hz, 40_000_000)

    def test_range_from_device_goes_to_profile(self):
        lib = fake_lib()
        lib.pluto._set_raw("ad9361-phy", "", False, "xo_correction_available", "[39996000 1 40004000]")
        self.lib, self.pl = lib, lib.pluto
        self.dev = pd.PlutoSdrDevice(lib=lib)
        self.dev.open(FAKE_URI, xo_ppm_by_serial={FAKE_SERIAL: 150.0})
        self.assertEqual((self.dev.profile()["xo"]["min_hz"], self.dev.profile()["xo"]["max_hz"]),
                         (39_996_000, 40_004_000))
        self.assertEqual(self.dev.xo_state["refused_ppm"], 150.0)
        self.assertEqual(xo_writes(self.pl), [])

    def test_device_without_attribute(self):
        self.open(1.5, xo_attr=False)
        self.assertTrue(self.dev.is_open)
        st = self.dev.xo_state
        self.assertEqual((st["supported"], st["hz"], st["refused_ppm"]), (False, None, None))
        self.assertFalse(self.dev.set_xo_ppm(2.0))
        raw = self.dev.measure(self.adopt())
        self.assertEqual((raw.extra["ppm"], raw.extra["xo_correction_hz"]), (0.0, None))

    def test_runtime_write_rewrites_rate_and_lo_on_next_measure(self):
        self.open()
        a = self.adopt()
        _, plan = make_plan(self.dev.profile(), a.axis, a.rbw_hz)
        self.assertEqual(len(plan.centers), 1)  # 位置が1つ: 同じ条件の2回目は LO を書かない
        rx_lo = ("ad9361-phy/altvoltage0_out", "frequency")
        self.dev.measure(a)
        n = len(self.pl.log)
        self.dev.measure(a)
        self.assertEqual([w for w in self.pl.log[n:] if w[:2] == rx_lo], [])
        self.assertTrue(self.dev.set_xo_ppm(-2.5))
        self.assertEqual(self.pl.xo_hz, 39_999_900)
        k = self.pl.log.index((*XO, "39999900"))
        self.assertEqual(self.pl.log[k + 1][:2], ("ad9361-phy/voltage0", "sampling_frequency"))
        raw = self.dev.measure(a)
        self.assertTrue([w for w in self.pl.log[k:] if w[:2] == rx_lo], "受信の LO を書き直していない")
        self.assertEqual((raw.extra["ppm"], raw.extra["xo_correction_hz"]), (-2.5, 39_999_900))
        self.assertEqual(raw_extra_for(raw)["xo_correction_hz"], 39_999_900)
        self.assertEqual(raw_extra_for(raw)["ppm"], -2.5)

    def test_runtime_write_retunes_tg_tx_lo(self):
        self.open()
        a = self.adopt(mode="tg")
        tx_lo = ("ad9361-phy/altvoltage1_out", "frequency")
        self.dev.measure(a)
        n = len(self.pl.log)
        self.dev.measure(a)
        self.assertEqual([w for w in self.pl.log[n:] if w[:2] == tx_lo], [])  # 同じ条件なら送信の LO は書かない
        self.dev.set_xo_ppm(3.0)
        k = len(self.pl.log)
        self.dev.measure(a)
        self.assertTrue([w for w in self.pl.log[k:] if w[:2] == tx_lo], "送信の LO を書き直していない")

    def test_zero_at_runtime_writes_nominal(self):
        lib = fake_lib()
        lib.pluto._set_raw("ad9361-phy", "", False, "xo_correction", "40000123")  # 前の接続で書いた値が残っている形
        self.lib, self.pl = lib, lib.pluto
        self.dev = pd.PlutoSdrDevice(lib=lib)
        self.dev.open(FAKE_URI)
        self.assertEqual(xo_writes(self.pl), ["40000000"])
        self.dev.set_xo_ppm(1.0)
        self.assertEqual(self.pl.xo_hz, 40_000_040)
        self.dev.set_xo_ppm(0.0)
        self.assertEqual(self.pl.xo_hz, 40_000_000)
        self.assertEqual(self.dev.xo_state["ppm"], 0.0)

    def test_runtime_out_of_range_and_ext_tx(self):
        self.open()
        n0 = len(xo_writes(self.pl))  # 繋いだときの 1 回（公称の値）
        with self.assertRaises(xo.XoOutOfRange):
            self.dev.set_xo_ppm(201.0)
        self.assertEqual(xo_writes(self.pl)[n0:], [])
        self.dev._ext = object()  # 外部 IQ の送信の間（押すスレッドがいる形）
        try:
            with self.assertRaises(InvalidSweep):
                self.dev.set_xo_ppm(1.0)
        finally:
            self.dev._ext = None
        self.assertEqual(xo_writes(self.pl)[n0:], [])


class TestInstrumentXo(PlutoCase):
    def xo(self) -> dict:
        return self.call("get_state")["plutosdr"]["xo"]

    def connect(self):
        self.call("connect", backend="plutosdr", serial=FAKE_SERIAL)

    def wait_state(self, pred, timeout=10.0, what="状態"):
        end = time.monotonic() + timeout
        while True:
            st = self.call("get_state")
            if pred(st):
                return st
            self.assertLess(time.monotonic(), end, f"{what} にならない")
            time.sleep(0.05)

    def test_state_when_connected(self):
        self.connect()
        x = self.xo()
        self.assertEqual((x["serial"], x["ppm"], x["live"], x["supported"], x["hz"]),
                         (FAKE_SERIAL, 0.0, True, True, 40_000_000))
        self.assertEqual((x["min_ppm"], x["max_ppm"], x["range_hz"]), (-200.0, 200.0, [39_992_000, 40_008_000]))
        self.assertIsNone(x["lock"])

    def test_set_while_stopped_writes_and_rewrites_on_next_sweep(self):
        self.connect()
        self.single()
        pl = self.lib.pluto
        res = self.call("plutosdr_xo_set", ppm=1.2345)
        self.assertEqual(res["plutosdr"]["xo"]["ppm"], 1.234)  # 小数 3 桁
        self.assertEqual(pl.xo_hz, xo.hz_for(1.234))
        self.assertEqual(res["plutosdr"]["xo"]["hz"], xo.hz_for(1.234))
        k = max(i for i, w in enumerate(pl.log) if w[:2] == XO)
        self.single()
        self.assertTrue([w for w in pl.log[k:] if w[:2] == ("ad9361-phy/altvoltage0_out", "frequency")])
        self.assertEqual(self.inst._last_raw.extra["ppm"], 1.234)
        self.assertEqual(self.inst._last_raw.extra["xo_correction_hz"], xo.hz_for(1.234))
        saved = json.loads((self.root / "data" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["plutosdr"]["xo_ppm"], {FAKE_SERIAL: 1.234})
        self.assertEqual(saved["plutosdr"]["xo_serial"], FAKE_SERIAL)

    def test_refused_while_sweeping(self):
        self.connect()
        n0 = len(xo_writes(self.lib.pluto))
        self.call("run", mode="continuous")
        self.assertEqual(self.xo()["lock"], "plutosdr_xo_busy")
        with self.assertRaises(CommandError) as cm:
            self.call("plutosdr_xo_set", ppm=2.0)
        self.assertEqual(cm.exception.code, "plutosdr_xo_busy")
        self.assertIn(cm.exception.code, texts.ERRORS)
        self.assertEqual(xo_writes(self.lib.pluto)[n0:], [])
        self.call("stop")
        self.wait_state(lambda st: st["run"]["state"] == "stopped", what="止まる")
        self.call("plutosdr_xo_set", ppm=2.0)
        self.assertEqual(self.lib.pluto.xo_hz, 40_000_080)

    def test_range_and_bad_values(self):
        self.connect()
        n0 = len(xo_writes(self.lib.pluto))
        for bad in (200.5, -201.0, "1", True, float("nan")):
            with self.assertRaises(CommandError) as cm:
                self.call("plutosdr_xo_set", ppm=bad)
            self.assertEqual(cm.exception.code, "bad_value")
        self.assertEqual(xo_writes(self.lib.pluto)[n0:], [])

    def test_not_connected_remembers_and_writes_on_next_connect(self):
        # PlutoSDR に繋いだことが無い: どの機器の値か決まらないので断る（serial を渡せば覚える）
        with self.assertRaises(CommandError) as cm:
            self.call("plutosdr_xo_set", ppm=1.0)
        self.assertEqual(cm.exception.code, "plutosdr_not_connected")
        self.connect()
        self.call("disconnect")
        x = self.xo()
        self.assertEqual((x["serial"], x["live"], x["hz"]), (FAKE_SERIAL, False, None))
        n0 = len(xo_writes(self.lib.pluto))
        self.call("plutosdr_xo_set", ppm=-3.0)  # 最後に繋いだ PlutoSDR の値として覚える
        self.assertEqual(xo_writes(self.lib.pluto)[n0:], [])
        self.assertEqual(self.xo()["ppm"], -3.0)
        self.connect()
        self.assertEqual(self.lib.pluto.xo_hz, 39_999_880)
        self.assertEqual(self.xo()["hz"], 39_999_880)

    def test_values_per_serial(self):
        self.call("plutosdr_xo_set", ppm=5.0, serial="PLUTO2")
        self.call("plutosdr_xo_set", ppm=-1.0, serial=FAKE_SERIAL)
        self.connect()
        self.assertEqual(self.lib.pluto.xo_hz, 39_999_960)
        self.call("disconnect")
        other = FakeIioLib(FakePluto(signals=[], serial="PLUTO2"))
        self.inst._backends["plutosdr"]._lib_factory = lambda: other
        self.call("connect", backend="plutosdr", port=FAKE_URI)
        self.assertEqual(other.pluto.xo_hz, 40_000_200)
        x = self.xo()
        self.assertEqual((x["serial"], x["ppm"]), ("PLUTO2", 5.0))
        # 繋いでいない機器の値を変えても、繋いでいる機器には書かない
        n = len(xo_writes(other.pluto))
        self.call("plutosdr_xo_set", ppm=7.0, serial=FAKE_SERIAL)
        self.assertEqual(len(xo_writes(other.pluto)), n)
        self.assertEqual(self.inst._pluto_cfg["xo_ppm"], {"PLUTO2": 5.0, FAKE_SERIAL: 7.0})

    def test_refused_on_connect_is_noticed(self):
        self.call("plutosdr_xo_set", ppm=150.0, serial=FAKE_SERIAL)
        self.lib.pluto._set_raw("ad9361-phy", "", False, "xo_correction_available", "[39996000 1 40004000]")
        self.connect()
        p = self.wait_for(lambda k, p: k == "notice" and p["code"] == "plutosdr_xo_refused")
        self.assertEqual((p["detail"]["ppm"], p["detail"]["min"], p["detail"]["max"]), (150.0, -100.0, 100.0))
        self.assertIn("plutosdr_xo_refused", texts.NOTICES)
        x = self.xo()
        self.assertEqual((x["refused_ppm"], x["hz"], x["min_ppm"], x["max_ppm"]), (150.0, 40_000_000, -100.0, 100.0))
        self.assertEqual(self.call("get_state")["connection"]["state"], "connected")

    def test_device_without_attribute(self):
        self.lib = fake_lib(xo_attr=False)
        self.inst._backends["plutosdr"]._lib_factory = lambda: self.lib
        self.connect()
        self.assertFalse(self.xo()["supported"])
        with self.assertRaises(CommandError) as cm:
            self.call("plutosdr_xo_set", ppm=1.0)
        self.assertEqual(cm.exception.code, "plutosdr_xo_unsupported")
        self.single()  # 測れる

    def test_settings_survive_restart(self):
        from namibench.core.instrument_pluto_xo import load_xo_settings

        self.assertEqual(load_xo_settings({"xo_ppm": {"A": 1.23456, "B": "x", "": 1.0, "C": 0.0}, "xo_serial": "A"}),
                         {"xo_ppm": {"A": 1.235}, "xo_serial": "A"})
        self.assertEqual(load_xo_settings({}), {"xo_ppm": {}, "xo_serial": None})


class TestExtTxXo(ExtCase):
    """外部 IQ の送信の間は、掃引を止めていても変えさせない（出している IQ の周波数が変わる）。"""

    def test_refused_while_streaming(self):
        self.streaming()
        n0 = len(xo_writes(self.pl))
        self.call("stop")
        self.until(lambda st: st["run"]["state"] == "stopped", what="掃引が止まる")
        self.assertEqual(self.state()["plutosdr"]["xo"]["lock"], "ext_tx_active")
        with self.assertRaises(CommandError) as cm:
            self.call("plutosdr_xo_set", ppm=1.0)
        self.assertEqual(cm.exception.code, "ext_tx_active")
        self.assertEqual(xo_writes(self.pl)[n0:], [])


if __name__ == "__main__":
    unittest.main()
