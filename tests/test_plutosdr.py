"""PlutoSDR（設計Q §7）の試験。libiio の代わりに偽物（devices/plutosdr/fake.py）を使う。実機は使わない。"""
from __future__ import annotations

import socket
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path

import numpy as np

from pygigast.api.local import _PlutoSdrBackend, _SimulatorBackend
from pygigast.core.devices.base import load_profile
from pygigast.core.devices.plutosdr import driver as pd
from pygigast.core.devices.plutosdr import tones
from pygigast.core.devices.plutosdr.fake import FAKE_SERIAL, FAKE_URI, FakeIioLib, FakePluto
from pygigast.core.devices.plutosdr.iio import dll_bits, find_dll
from pygigast.core.devices.rtlsdr import levels, spectrum
from pygigast.core.instrument import GIGAST_PROFILE_PATH, Instrument
from pygigast.core.instrument_common import CommandError
from pygigast.core.rtltcp.decimate import IqDecimator, factor_for
from pygigast.core.sweep.model import SweepModel
from pygigast.core.types import DeviceDisconnected, Mode, SweepRequest

MHZ = 1_000_000
UNCAL = "dB（未校正）"


def fake_lib(signals=((84_700_000, -40.0),), **kw) -> FakeIioLib:
    return FakeIioLib(FakePluto(signals=list(signals), **kw))


class TestProfileAndPieces(unittest.TestCase):
    def test_rbw_values_match_windows(self):
        prof = pd.default_profile()
        fs = prof["fft"]["sample_rate_hz"]
        for c in prof["rbw"]["choices"]:
            self.assertEqual(c["rbw_hz"], spectrum.rbw_hz(c["window"], c["fft_n"], fs))
            self.assertEqual(fs % c["fft_n"], 0)  # 格子は Hz の整数（設計Q §6.4）

    def test_passband_table_shape(self):
        p = spectrum.Passband.from_profile(pd.default_profile())
        self.assertAlmostEqual(float(p.db(np.array([300_000]))[0]), -0.29, places=2)
        h = spectrum.half_usable_hz(4_096_000, 0.66, 1000)
        self.assertLessEqual(h, 1_450_000)  # 表は使える幅の端まである

    def test_multitone_on_every_bin_and_flat_with_rect(self):
        n, grid, half, delta = 1024, 4000, 1_350_000, 200_000
        x = tones.multitone(n, grid, half, delta, 0.5)
        self.assertAlmostEqual(float(np.max(np.abs(x))), 0.5, places=6)
        p = spectrum.averaged_power(np.tile(x, 4), n, spectrum.RECT, 4)
        k = np.arange(-(n // 2), n - n // 2)
        on = (np.abs(k) * grid >= delta) & (np.abs(k) * grid <= half)
        db = 10 * np.log10(p[on])
        self.assertLess(float(db.max() - db.min()), 0.01)
        self.assertLess(float(10 * np.log10(p[~on].max() + 1e-30)), float(db.min()) - 100)

    def test_overload_and_iq(self):
        a = np.array([2047, 0, 0, 0, -2048, 5, 3, 3], dtype=np.int16)
        self.assertAlmostEqual(pd.overload_ratio_i16(a, 2048), 0.5)
        z = pd.iq_from_i16(np.array([1024, -2048], dtype=np.int16), 2048)
        self.assertAlmostEqual(complex(z[0]), 0.5 - 1j)

    def test_gain_limits_by_band(self):
        prof = pd.default_profile()
        self.assertEqual(pd.gain_limits(prof, [100 * MHZ]), (-1, 73))
        self.assertEqual(pd.gain_limits(prof, [1_200 * MHZ, 1_400 * MHZ]), (-1, 71))
        self.assertEqual(pd.gain_limits(prof, [5_000 * MHZ]), (-10, 62))

    def test_parse_range(self):
        self.assertEqual(pd.parse_range("[-89.750000 0.250000 0.000000]"), (-89.75, 0.25, 0.0))
        self.assertIsNone(pd.parse_range("manual"))

    def test_decimator(self):
        self.assertEqual(factor_for(4_096_000, 2_048_000), 2)
        self.assertIsNone(factor_for(4_096_000, 2_400_000))
        d = IqDecimator(4_096_000, 2_048_000)
        t = np.arange(8192) / 4_096_000
        x = np.exp(2j * np.pi * 100_000 * t).astype(np.complex64)
        y = np.concatenate([d.process(x[:3001]), d.process(x[3001:])])
        self.assertEqual(y.size, 4096)
        self.assertAlmostEqual(float(np.abs(y[200:]).mean()), 1.0, delta=0.02)
        self.assertFalse(d.set_out_rate(3_000_000))

    def test_dll_search_finds_nothing_in_empty_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = find_dll(tmp)
            self.assertTrue(p is None or Path(p).name.lower().startswith("libiio"))
            self.assertIsNone(dll_bits(Path(tmp) / "nothing.dll"))


class TestDevice(unittest.TestCase):
    def setUp(self):
        self.lib = fake_lib()
        self.dev = pd.PlutoSdrDevice(lib=self.lib)
        self.dev.open(FAKE_URI)
        self.prof = self.dev.profile()
        self.model = SweepModel(self.prof, pd.timing_fn(self.prof))

    def tearDown(self):
        self.dev.close()

    def test_discover_merges_usb_and_ip(self):
        rows = pd.discover(self.lib, (FAKE_URI,), cache=pd._SerialCache())
        self.assertEqual([(r.port, r.serial) for r in rows], [(FAKE_URI, FAKE_SERIAL)])
        rows = pd.discover(self.lib, ("ip:10.0.0.9",), cache=pd._SerialCache())
        self.assertEqual([r.port for r in rows], ["usb:9.9.9"])

    def test_ranges_from_device(self):
        self.assertEqual(self.prof["freq"]["min_hz"], 70 * MHZ)
        self.assertEqual(self.prof["source"]["sg"]["min_hz"], 46_875_001 + 1_000_000)
        self.assertEqual(self.prof["gain"]["bands"][2]["max_db"], 62)

    def test_sa_level_is_dbfs_minus_gain_and_tx_untouched(self):
        a = self.model.default()
        r = self.dev.measure(a)
        lv, unit = levels.to_level(r.raw, r.extra["gain_db"])
        f = a.axis.freqs()
        i = int(np.nanargmax(np.where(np.abs(f - 84_700_000) < 20_000, lv, -999)))
        self.assertEqual(f[i], 84_700_000)
        self.assertAlmostEqual(float(lv[i]), -40.0, delta=1.2)
        self.assertEqual(unit, UNCAL)
        self.assertEqual(self.lib.pluto.tx_writes(), [])  # 受信だけのときは送信に触らない
        self.assertFalse(np.isnan(r.raw).any())

    def test_gain_clamped_to_band(self):
        self.dev.set_gain(73)
        a = self.model.apply(SweepRequest(start_hz=2_400 * MHZ, stop_hz=2_402 * MHZ, edited=("start_hz", "stop_hz")),
                             self.model.default())
        r = self.dev.measure(a)
        self.assertEqual(r.extra["gain_db"], 71.0)
        self.assertEqual(r.extra["gain_setting_db"], 73.0)

    def test_sg_tone_then_idle_turns_tx_off(self):
        a = self.model.apply(SweepRequest(mode=Mode.SG, sg_hz=88 * MHZ), self.model.default())
        self.dev.set_tx_atten(-20)
        r = self.dev.measure(a)
        lv, _ = levels.to_level(r.raw, r.extra["gain_db"])
        f = a.axis.freqs()
        i = int(np.nanargmax(np.where(np.abs(f - 88 * MHZ) < 20_000, lv, -999)))
        self.assertEqual(f[i], 88 * MHZ)
        # 減衰 −20dB・直結の損失 30dB・DDS の大きさ 0.5（−6dB）
        self.assertAlmostEqual(float(lv[i]), -56.0, delta=1.5)
        self.assertEqual(r.extra["tx"], "sg")
        pl = self.lib.pluto
        self.assertEqual(pl.attrs[("ad9361-phy", "altvoltage1", True, "frequency")], str(87 * MHZ))
        self.dev.on_idle()
        self.assertEqual(pl.attrs[("ad9361-phy", "altvoltage1", True, "powerdown")], "1")
        self.assertIsNone(self.dev.tx_on)

    def test_tg_follows_rx_and_ratio_is_flat(self):
        a = self.model.apply(SweepRequest(mode=Mode.TG), self.model.default())
        self.dev.set_tx_atten(-10)  # 被測定物で 20dB 落としても雑音より十分上
        r = self.dev.measure(a)
        self.assertEqual(r.extra["window"], spectrum.RECT)
        lv, _ = levels.to_level(r.raw, r.extra["gain_db"])
        self.assertLess(float(np.nanpercentile(lv, 99) - np.nanpercentile(lv, 1)), 3.0)
        # 送信の LO は最後の位置の受信の LO
        self.assertEqual(self.lib.pluto.attrs[("ad9361-phy", "altvoltage1", True, "frequency")],
                         str(r.extra["centers_hz"][-1]))
        # 通過特性のある被測定物: 比は被測定物の形になる
        self.lib.pluto.dut = lambda fr: np.where(np.asarray(fr) > 85 * MHZ, 0.1, 1.0)
        r2 = self.dev.measure(a)
        lv2, _ = levels.to_level(r2.raw, r2.extra["gain_db"])
        ratio = lv2 - lv
        f = a.axis.freqs()
        self.assertAlmostEqual(float(np.nanmedian(ratio[f < 84 * MHZ])), 0.0, delta=0.5)
        self.assertAlmostEqual(float(np.nanmedian(ratio[f > 86 * MHZ])), -20.0, delta=0.5)

    def test_fit_gain_largest_without_overload(self):
        a = self.model.default()
        res = self.dev.fit_gain(a)
        # −40dBm は 40dB で振り切れの手前（dBFS = 入力 + 利得）
        self.assertIn(res["gain_db"], (38.0, 39.0, 40.0))
        self.assertFalse(res["overload"])
        self.assertLessEqual(res["steps_tried"], 9)

    def test_unplug_is_disconnect(self):
        self.lib.pluto.unplugged = True
        with self.assertRaises(DeviceDisconnected):
            self.dev.measure(self.model.default())


class PlutoCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.lib = fake_lib()
        pd._cache = pd._SerialCache()
        self.profile = load_profile(GIGAST_PROFILE_PATH)
        sim = _SimulatorBackend(self.profile)
        sim.speed = 0.05
        pl = _PlutoSdrBackend(lib_factory=lambda: self.lib)
        self.inst = Instrument({"simulator": sim, "plutosdr": pl}, profile=self.profile,
                               data_root=self.root / "data", cal_root=self.root / "cal")
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

    def single(self):
        with self.cv:
            self.events.clear()
        self.call("run", mode="single")
        self.wait_for(lambda k, p: k == "state" and p["run"]["state"] == "stopped"
                      and p["run"]["last_stop_reason"] == "single_done")
        return self.call("get_trace", id=1)


class TestInstrument(PlutoCase):
    def test_device_list_and_connect(self):
        rows = self.call("list_devices")["devices"]
        pr = [r for r in rows if r["kind"] == "plutosdr"]
        self.assertEqual(len(pr), 1)
        self.assertEqual(pr[0]["port"], FAKE_URI)
        self.call("connect", backend="plutosdr", serial=FAKE_SERIAL)
        st = self.call("get_state")
        self.assertEqual(st["connection"]["profile_model"], "plutosdr-ad9364")
        self.assertEqual(st["connection"]["sweep_kind"], "fft")
        self.assertEqual((st["sweep"]["start_hz"], st["sweep"]["stop_hz"]), (76 * MHZ, 95 * MHZ))
        self.assertTrue(st["plutosdr"]["connected"])
        self.assertEqual(st["plutosdr"]["gain_limits_db"], [-1, 73])
        tr = self.single()
        self.assertEqual(tr["unit"], UNCAL)

    def test_dll_not_found_row(self):
        inst_b = self.inst._backends["plutosdr"]
        from pygigast.core.devices.plutosdr.iio import IioNotFound

        def missing():
            raise IioNotFound("x")

        inst_b._lib_factory = missing
        rows = self.call("list_devices")["devices"]
        self.assertEqual([r["state"] for r in rows if r["kind"] == "plutosdr"], ["dll_not_found"])
        with self.assertRaises(CommandError) as cm:
            self.call("list_devices", backend="plutosdr")
        self.assertEqual(cm.exception.code, "plutosdr_dll_not_found")

    def test_settings_gain_atten_and_sg(self):
        self.call("connect", backend="plutosdr", serial=FAKE_SERIAL)
        res = self.call("plutosdr_set", gain_db=20.0, tx_atten_db=-30.1)
        self.assertEqual(res["plutosdr"]["gain_db"], 20.0)
        self.assertEqual(res["plutosdr"]["tx_atten_db"], -30.0)  # 0.25dB 刻み
        with self.assertRaises(CommandError):
            self.call("plutosdr_set", tx_atten_db=5.0)
        self.call("set_mode", mode="sg", sg_hz=90_500_000)
        self.single()
        st = self.call("get_state")
        self.assertEqual(st["sweep"]["mode"], "sg")
        # 止まったら送信も止まる（機器スレッドの on_idle）
        end = time.monotonic() + 5
        while self.lib.pluto.attrs[("ad9361-phy", "altvoltage1", True, "powerdown")] != "1":
            self.assertLess(time.monotonic(), end)
            time.sleep(0.02)
        res = self.call("plutosdr_set", agc=True)
        self.assertTrue(res["plutosdr"]["agc"])
        self.assertEqual(self.single()["unit"], "dBFS")

    def test_tg_reference_fine_only(self):
        self.call("connect", backend="plutosdr", serial=FAKE_SERIAL)
        self.call("set_mode", mode="tg")
        # 偽物の直結（損失 30dB）で多トーンが雑音より十分上（約 50dB）になる減衰にする（比の揺れを雑音で決めない）
        self.call("plutosdr_set", tx_atten_db=-10.0)
        with self.assertRaises(CommandError) as cm:
            self.call("reference_take", kind="wide")
        self.assertEqual(cm.exception.code, "reference_wide_fft")
        out = self.call("reference_take", kind="fine")
        self.wait_for(lambda k, p: k == "notice" and p["code"] in ("task_done", "task_failed")
                      and (p["detail"].get("task") == "reference_take"))
        self.assertTrue(out["task_id"])
        tr = self.single()
        v = np.asarray(tr["values"], dtype=float)
        self.assertLess(float(np.nanmax(np.abs(v))), 1.5)  # 基準との比は 0dB の近く

    def test_fit_gain_task(self):
        self.call("connect", backend="plutosdr", serial=FAKE_SERIAL)
        self.call("plutosdr_fit_gain")
        p = self.wait_for(lambda k, p: k == "notice" and p["code"] == "task_done"
                          and p["detail"].get("task") == "plutosdr_fit_gain")
        g = p["detail"]["result"]["gain_db"]
        self.assertEqual(self.call("get_state")["plutosdr"]["gain_db"], g)

    def test_rtltcp_sends_decimated_iq(self):
        self.call("connect", backend="plutosdr", serial=FAKE_SERIAL)
        port = _free_port()
        self.call("rtltcp_set", enabled=True, port=port)
        s = socket.create_connection(("127.0.0.1", port), timeout=10)
        try:
            head = _recv(s, 12)
            self.assertEqual(head[:4], b"RTL0")
            self.assertEqual(struct.unpack(">I", head[4:8])[0], 0)
            self.wait_for(lambda k, p: k == "state" and p["rtltcp"]["clients"] == 1)
            self.call("run", mode="continuous")
            data = _recv(s, 4096)
            self.assertEqual(len(data), 4096)
            st = self.call("get_state")
            self.assertLessEqual(st["sweep"]["stop_hz"] - st["sweep"]["start_hz"], 2 * 1_351_500)
        finally:
            s.close()
            self.call("stop")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _recv(sock, n, timeout=15.0):
    sock.settimeout(timeout)
    buf = b""
    while len(buf) < n:
        c = sock.recv(n - len(buf))
        if not c:
            raise ConnectionError("closed")
        buf += c
    return buf


if __name__ == "__main__":
    unittest.main()
