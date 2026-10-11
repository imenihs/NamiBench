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
        n0 = len(self.lib.pluto.tx_writes())  # つないだときに送信を止めた分（2026-10-11 ユーザー決定）は数えない
        r = self.dev.measure(a)
        lv, unit = levels.to_level(r.raw, r.extra["gain_db"])
        f = a.axis.freqs()
        i = int(np.nanargmax(np.where(np.abs(f - 84_700_000) < 20_000, lv, -999)))
        self.assertEqual(f[i], 84_700_000)
        self.assertAlmostEqual(float(lv[i]), -40.0, delta=1.2)
        self.assertEqual(unit, UNCAL)
        self.assertEqual(self.lib.pluto.tx_writes()[n0:], [])  # 受信だけのときは送信に触らない
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


def _output_on_wrong(log, start_attrs, target_lo, atten_min=-89.75):
    """送信の書き込みを順に再生し、「出力が出ている（LO が動作・DDS の大きさ > 0 か循環バッファあり）のに、減衰が最小より
    大きく、LO が目的の周波数でない」瞬間があれば、その書き込みを返す（無ければ None）。"""
    pd_ = start_attrs["powerdown"]
    att = start_attrs["atten"]
    lo = start_attrs["lo"]
    scale = start_attrs["scale"]
    buf = False
    for dev, name, val in log:
        if dev == "ad9361-phy/altvoltage1_out" and name == "powerdown":
            pd_ = str(val)
        elif dev == "ad9361-phy/altvoltage1_out" and name == "frequency":
            lo = int(val)
        elif dev == "ad9361-phy/voltage0_out" and name == "hardwaregain":
            att = float(val)
        elif dev == "cf-ad9361-dds-core-lpc/altvoltage0_out" and name == "scale":
            scale = float(val)
        elif dev == "cf-ad9361-dds-core-lpc/buffer_out":
            buf = name == "push"
        on = pd_ == "0" and (scale > 0 or buf)
        if on and att > atten_min and lo != target_lo:
            return (dev, name, val, {"lo": lo, "atten": att})
    return None


class TestTxSafety(unittest.TestCase):
    """送信の安全（レビュー 1巡目）: 始める順番と、アプリを終えるときに止め終えること。"""

    def setUp(self):
        self.lib = fake_lib()
        self.dev = pd.PlutoSdrDevice(lib=self.lib)
        self.dev.open(FAKE_URI)
        self.model = SweepModel(self.dev.profile(), pd.timing_fn(self.dev.profile()))
        # 電源を入れた直後の状態（実機で読んだ値: 送信の LO 2450MHz で動作・減衰 −10dB・DDS の大きさ 0）
        self.start = {"powerdown": "0", "atten": -10.0, "lo": 2_450_000_000, "scale": 0.0}

    def tearDown(self):
        self.dev.close()

    def tx_log(self, n0):
        return [w for w in self.lib.pluto.log[n0:] if w[0].startswith("cf-ad9361-dds-core-lpc")
                or w[0] in ("ad9361-phy/altvoltage1_out", "ad9361-phy/voltage0_out")]

    def check_order(self, log, target_lo, user_atten):
        self.assertEqual(log[0][:2], ("ad9361-phy/voltage0_out", "hardwaregain"))  # まず出力をいちばん小さく
        self.assertEqual(float(log[0][2]), -89.75)
        self.assertIsNone(_output_on_wrong(log, self.start, target_lo))
        names = [(w[0], w[1]) for w in log]
        i_lo = names.index(("ad9361-phy/altvoltage1_out", "frequency"))
        i_up = max(i for i, n in enumerate(names) if n == ("ad9361-phy/altvoltage1_out", "powerdown") and log[i][2] in (0, "0"))
        self.assertLess(i_lo, i_up)
        self.assertEqual(log[-1][:2], ("ad9361-phy/voltage0_out", "hardwaregain"))  # 利用者の減衰は最後
        self.assertEqual(float(log[-1][2]), user_atten)

    def test_connect_turns_the_power_up_tx_off(self):
        # つないだら送信を止める（2026-10-11 ユーザー決定）: 電源投入時の 2450MHz の LO（動作・減衰 −10dB）を止める
        ph = self.lib.pluto
        tx_writes = [e for e in ph.log if e[0] in ("ad9361-phy/altvoltage1_out", "ad9361-phy/voltage0_out")]
        self.assertIn(("ad9361-phy/altvoltage1_out", "powerdown"), [e[:2] for e in tx_writes])
        self.assertEqual(float([e for e in tx_writes if e[:2] == ("ad9361-phy/voltage0_out", "hardwaregain")][0][2]), -89.75)

    def test_sg_start_order_from_power_up(self):
        self.dev.set_tx_atten(-80)
        a = self.model.apply(SweepRequest(mode=Mode.SG, sg_hz=433 * MHZ), self.model.default())
        n0 = len(self.lib.pluto.log)
        self.dev.measure(a)
        self.check_order(self.tx_log(n0), 432 * MHZ, -80.0)

    def test_tg_start_order_from_power_up(self):
        self.dev.set_tx_atten(-80)
        a = self.model.apply(SweepRequest(mode=Mode.TG), self.model.default())
        p, pl = pd.make_plan(self.dev.profile(), a.axis, a.rbw_hz)
        n0 = len(self.lib.pluto.log)
        self.dev.measure(a)
        log = self.tx_log(n0)
        # 追従で LO が動く前（最初の位置）までの書き込みで確かめる
        first = [i for i, w in enumerate(log) if w[1] == "frequency" and w[0] == "ad9361-phy/altvoltage1_out"]
        upto = first[1] if len(first) > 1 else len(log)
        self.check_order(log[:upto], pl.centers[0], -80.0)

    def test_sg_frequency_change_while_on_is_muted(self):
        a = self.model.apply(SweepRequest(mode=Mode.SG, sg_hz=433 * MHZ), self.model.default())
        self.dev.set_tx_atten(-20)
        self.dev.measure(a)
        b = self.model.apply(SweepRequest(sg_hz=500 * MHZ), a)
        n0 = len(self.lib.pluto.log)
        self.dev.measure(b)
        st = {"powerdown": "0", "atten": -20.0, "lo": 432 * MHZ, "scale": 0.5}
        self.assertIsNone(_output_on_wrong(self.tx_log(n0), st, 499 * MHZ))

    def test_emergency_off_from_other_thread_while_measuring(self):
        a = self.model.apply(SweepRequest(mode=Mode.SG, sg_hz=433 * MHZ), self.model.default())
        self.dev.measure(a)
        pl = self.lib.pluto
        gate = threading.Event()
        orig = pl.samples

        def slow(n):
            gate.wait(5)
            return orig(n)

        pl.samples = slow
        t = threading.Thread(target=lambda: self._swallow(a), daemon=True)
        t.start()
        time.sleep(0.2)  # 測定が口を握って止まっている
        self.dev.emergency_tx_off(wait_s=0.1)
        self.assertEqual(pl.attrs[("ad9361-phy", "altvoltage1", True, "powerdown")], "1")
        gate.set()
        t.join(5)
        pl.samples = orig
        with self.assertRaises(DeviceDisconnected):
            self.dev.measure(a)  # 閉じる途中は送信を出し直さない
        self.assertEqual(pl.attrs[("ad9361-phy", "altvoltage1", True, "powerdown")], "1")

    def _swallow(self, a):
        try:
            self.dev.measure(a)
        except Exception:  # noqa: BLE001
            pass


class TestShutdownStopsTx(PlutoCase):
    def tearDown(self):
        if not getattr(self, "_shut", False):
            self.inst.shutdown()
        self.tmp.cleanup()

    def start_output(self, mode):
        self.call("connect", backend="plutosdr", serial=FAKE_SERIAL)
        self.call("set_mode", mode=mode, sg_hz=90_500_000)
        self.call("run", mode="continuous")
        pl = self.lib.pluto
        end = time.monotonic() + 5
        while pl.attrs[("ad9361-phy", "altvoltage1", True, "powerdown")] != "0":
            self.assertLess(time.monotonic(), end)
            time.sleep(0.02)
        return pl

    def shutdown_and_check(self, pl, slow=True):
        if slow:
            # 1回の読みを遅くして、機器スレッドが閉じる前に shutdown が戻る形を確実に作る（直す前は戻った時点で出力中）
            orig = pl.samples
            pl.samples = lambda n: (time.sleep(0.3), orig(n))[1]
            time.sleep(0.1)
        self.inst.shutdown()
        self._shut = True
        self.assertEqual(pl.attrs[("ad9361-phy", "altvoltage1", True, "powerdown")], "1")
        self.assertEqual(float(pl.attrs[("ad9361-phy", "voltage0", True, "hardwaregain")].split()[0]), -89.75)

    def test_shutdown_returns_after_sg_off(self):
        self.shutdown_and_check(self.start_output("sg"))

    def test_shutdown_returns_after_tg_off(self):
        self.shutdown_and_check(self.start_output("tg"))
    def test_shutdown_turns_tx_off_when_device_thread_hangs(self):
        import pygigast.core.instrument as ins

        pl = self.start_output("sg")
        gate = threading.Event()
        orig = pl.samples
        pl.samples = lambda n: (gate.wait(10), orig(n))[1]
        time.sleep(0.2)
        old = ins.SHUTDOWN_DEVICE_WAIT_S
        ins.SHUTDOWN_DEVICE_WAIT_S = 0.3
        try:
            self.shutdown_and_check(pl, slow=False)
        finally:
            ins.SHUTDOWN_DEVICE_WAIT_S = old
            gate.set()


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
