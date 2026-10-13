"""PlutoSDR の SG・TG での「利得を合わせる」（2026-10-12 ユーザー決定: 合わせる間は測るときと同じ送信を出す）の試験。
libiio の偽物（devices/plutosdr/fake.py）を使う。実機は使わない。"""
from __future__ import annotations

import threading
import time
import unittest

from namibench.core.devices.plutosdr import driver as pd
from namibench.core.devices.plutosdr.fake import FAKE_SERIAL, FAKE_URI, FakeIioLib, FakePluto
from namibench.core.devices.plutosdr.iio import IioError
from namibench.core.sweep.model import SweepModel
from namibench.core.types import DeviceDisconnected, DeviceError, Mode, SweepDiscarded, SweepRequest

from tests.test_plutosdr import PlutoCase

TX_PD = ("ad9361-phy", "altvoltage1", True, "powerdown")
TX_LO = ("ad9361-phy", "altvoltage1", True, "frequency")
RX_LO = ("ad9361-phy", "altvoltage0", True, "frequency")
DDS_SCALE = ("cf-ad9361-dds-core-lpc", "altvoltage0", True, "scale")


def _strong_lib() -> FakeIioLib:
    # 外からの信号は無し。送信と受信の直結の損失 10dB（減衰 0dB で約 −16dBm が受信に入る）
    return FakeIioLib(FakePluto(signals=[], loss_db=10.0))


class FitTxCase(unittest.TestCase):
    def setUp(self):
        self.lib = _strong_lib()
        self.pl = self.lib.pluto
        self.dev = pd.PlutoSdrDevice(lib=self.lib)
        self.dev.open(FAKE_URI)
        self.dev.set_tx_atten(0)
        self.model = SweepModel(self.dev.profile(), pd.timing_fn(self.dev.profile()))
        self.sa = self.model.default()
        self.sg = self.model.apply(SweepRequest(mode=Mode.SG, sg_hz=self.sa.center_hz), self.sa)
        self.tg = self.model.apply(SweepRequest(mode=Mode.TG), self.sa)
        self.reads = []  # 受信を読むたびの (送信が出ているか, 送信の LO, 受信の LO)
        self._orig = self.pl.samples
        self.hook = None

        def samples(n):
            on = self.pl.attrs[TX_PD] == "0" and (float(self.pl.attrs[DDS_SCALE]) > 0 or self.pl.tx_buffer is not None)
            self.reads.append((on, int(self.pl.attrs[TX_LO]), int(float(self.pl.attrs[RX_LO]))))
            if self.hook is not None:
                self.hook(len(self.reads))
            return self._orig(n)

        self.pl.samples = samples

    def tearDown(self):
        self.pl.samples = self._orig
        self.dev.close()

    def tx_is_off(self):
        return self.dev.tx_on is None and self.pl.attrs[TX_PD] == "1"

    def no_signal_gain(self):
        return self.dev.fit_gain(self.sa)["gain_db"]


class TestFitTransmits(FitTxCase):
    def test_sg_fit_does_not_overload_with_own_tone_and_stops_tx(self):
        top = self.no_signal_gain()
        self.assertTrue(self.tx_is_off())
        self.reads.clear()
        res = self.dev.fit_gain(self.sg)
        self.assertLess(res["gain_db"], top - 20)  # 信号の無い最大の利得ではない
        self.assertFalse(res["overload"])
        self.assertTrue(self.reads and all(r[0] for r in self.reads))  # 合わせる間は送信が出ている
        self.assertTrue(self.tx_is_off())  # 止めていたので、終わったら止める
        # 選んだ利得で SG を測ると振り切れない。余裕（FIT_MARGIN_DB）の分は振り切れず、その1段上で振り切れる
        self.dev.set_gain(res["gain_db"])
        self.assertFalse(self.dev.measure(self.sg).extra["overload"])
        self.dev.set_gain(res["gain_db"] + pd.FIT_MARGIN_DB)
        self.assertFalse(self.dev.measure(self.sg).extra["overload"])
        self.dev.set_gain(res["gain_db"] + pd.FIT_MARGIN_DB + 1)
        self.assertTrue(self.dev.measure(self.sg).extra["overload"])

    def test_tg_fit_follows_rx_lo_and_stops_tx(self):
        top = self.no_signal_gain()
        self.reads.clear()
        res = self.dev.fit_gain(self.tg)
        self.assertLess(res["gain_db"], top - 20)
        self.assertFalse(res["overload"])
        self.assertTrue(self.reads and all(on and tx == rx for on, tx, rx in self.reads))
        self.assertTrue(self.tx_is_off())
        self.dev.set_gain(res["gain_db"])
        self.assertFalse(self.dev.measure(self.tg).extra["overload"])
        self.dev.set_gain(res["gain_db"] + pd.FIT_MARGIN_DB)
        self.assertFalse(self.dev.measure(self.tg).extra["overload"])
        self.dev.set_gain(res["gain_db"] + pd.FIT_MARGIN_DB + 1)
        self.assertTrue(self.dev.measure(self.tg).extra["overload"])

    def test_sg_fit_start_order_is_the_safe_one(self):
        from tests.test_plutosdr import _output_on_wrong

        n0 = len(self.pl.log)
        self.dev.fit_gain(self.sg)
        log = [w for w in self.pl.log[n0:] if w[0].startswith("cf-ad9361-dds-core-lpc")
               or w[0] in ("ad9361-phy/altvoltage1_out", "ad9361-phy/voltage0_out")]
        self.assertEqual(log[0][:2], ("ad9361-phy/voltage0_out", "hardwaregain"))
        self.assertEqual(float(log[0][2]), -89.75)
        start = {"powerdown": "1", "atten": -89.75, "lo": 0, "scale": 0.0}
        on_part = log[:max(i for i, w in enumerate(log) if w[1] == "hardwaregain" and float(w[2]) == 0.0) + 1]
        self.assertIsNone(_output_on_wrong(on_part, start, int(self.sg.sg_hz) - 1_000_000))

    def test_running_keeps_tx_on_and_next_measure_does_not_restart(self):
        for a, kind in ((self.sg, "sg"), (self.tg, "tg")):
            self.dev.measure(a)  # 連続で測っている（送信が出ている）
            self.assertEqual(self.dev.tx_on, kind)
            self.dev.fit_gain(a)
            self.assertEqual(self.dev.tx_on, kind)
            self.assertEqual(self.pl.attrs[TX_PD], "0")
            n0 = len(self.pl.log)
            self.dev.measure(a)
            self.assertNotIn(("ad9361-phy/altvoltage1_out", "powerdown", "1"), self.pl.log[n0:])

    def test_stop_during_fit_turns_tx_off(self):
        self.dev.measure(self.tg)
        self.reads.clear()
        self.hook = lambda k: self.dev.on_idle() if k == 2 else None  # 合わせている間に掃引が止まった
        self.dev.fit_gain(self.tg)
        self.assertTrue(self.tx_is_off())

    def test_cancel_stops_tx_when_stopped_and_keeps_it_when_running(self):
        for running in (False, True):
            if running:
                self.dev.measure(self.sg)
            cancel = threading.Event()
            self.reads.clear()
            self.hook = lambda k: cancel.set() if k == 1 else None
            with self.assertRaises(SweepDiscarded):
                self.dev.fit_gain(self.sg, cancel)
            self.hook = None
            if running:
                self.assertEqual(self.dev.tx_on, "sg")
            else:
                self.assertTrue(self.tx_is_off())

    def test_failure_stops_tx(self):
        def fail(k):
            if k == 2:
                raise IioError("buffer_refill", -22, "Invalid argument")

        self.hook = fail
        with self.assertRaises(DeviceError):
            self.dev.fit_gain(self.tg)
        self.assertTrue(self.tx_is_off())

    def test_unplug_during_fit(self):
        def unplug(k):
            if k == 2:
                self.pl.unplugged = True

        self.hook = unplug
        with self.assertRaises(DeviceDisconnected):
            self.dev.fit_gain(self.sg)
        self.assertFalse(self.dev.is_open)
        self.assertIsNone(self.dev.tx_on)

    def test_sa_fit_does_not_touch_tx(self):
        n0 = len(self.pl.tx_writes())
        self.dev.fit_gain(self.sa)
        self.assertEqual(self.pl.tx_writes()[n0:], [])
        self.assertFalse(any(r[0] for r in self.reads))


class TestFitTxInstrument(PlutoCase):
    def setUp(self):
        super().setUp()
        self.lib.pluto.signals = []
        self.lib.pluto.loss_db = 10.0
        self.call("connect", backend="plutosdr", serial=FAKE_SERIAL)
        self.call("plutosdr_set", tx_atten_db=0.0)

    def fit(self):
        with self.cv:
            self.events.clear()
        self.call("plutosdr_fit_gain")
        p = self.wait_for(lambda k, p: k == "notice" and p["code"] in ("task_done", "task_failed")
                          and p["detail"].get("task") == "plutosdr_fit_gain")
        self.assertEqual(p["code"], "task_done")
        return p["detail"]["result"]["gain_db"]

    def test_stopped_sg_and_tg_fit_then_tx_off(self):
        for mode in ("sg", "tg"):
            self.call("set_mode", mode=mode, sg_hz=85_500_000)
            g = self.fit()
            self.assertLessEqual(g, 20.0)
            self.assertEqual(self.lib.pluto.attrs[TX_PD], "1")
            self.assertEqual(self.call("get_state")["plutosdr"]["gain_db"], g)
            self.assertIsNone(self.call("get_state")["plutosdr"]["tx_on"])

    def test_output_shown_while_fitting_when_stopped(self):
        """止めた SG で合わせている間も、出していることが状態に出る（設計L: 出ていることが画面で分かる）。"""
        self.call("set_mode", mode="sg", sg_hz=85_500_000)
        self.assertEqual(self.call("get_state")["source"]["output"], "none")
        with self.cv:
            self.events.clear()
        self.call("plutosdr_fit_gain")
        self.wait_for(lambda k, p: k == "state" and (p.get("source") or {}).get("output") == "sg"
                      and (p.get("plutosdr") or {}).get("fitting"))
        self.wait_for(lambda k, p: k == "notice" and p["code"] == "task_done"
                      and p["detail"].get("task") == "plutosdr_fit_gain")
        end = time.monotonic() + 5
        while self.call("get_state")["source"]["output"] != "none":
            self.assertLess(time.monotonic(), end)
            time.sleep(0.02)

    def test_running_tg_fit_keeps_output_then_stop_turns_it_off(self):
        self.call("set_mode", mode="tg")
        self.call("run", mode="continuous")
        g = self.fit()
        self.assertLessEqual(g, 20.0)
        end = time.monotonic() + 5
        while self.lib.pluto.attrs[TX_PD] != "0":  # 連続のまま測り続ける
            self.assertLess(time.monotonic(), end)
            time.sleep(0.02)
        self.call("stop")
        end = time.monotonic() + 5
        while self.lib.pluto.attrs[TX_PD] != "1":
            self.assertLess(time.monotonic(), end)
            time.sleep(0.02)


if __name__ == "__main__":
    unittest.main()
