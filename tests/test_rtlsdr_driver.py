"""RTL-SDR の機器の口（設計Q §6.2〜§6.6・§6.9 の手順）の試験。DLL の代わりに合成の IQ を返す偽物を使う。"""
from __future__ import annotations

import threading
import unittest

import numpy as np

from pygigast.core.devices.rtlsdr import dll, driver, levels
from pygigast.core.devices.rtlsdr import spectrum as sp
from pygigast.core.sweep.model import SweepModel
from pygigast.core.types import DeviceDisconnected, DeviceError, InvalidSweep, SweepDiscarded, SweepRequest

FS = 2_048_000
MHZ = 1_000_000


class FakeHandle:
    """開いた機器の偽物。中心周波数に対する相対の周波数で合成の IQ を作る。"""

    def __init__(self, lib, index):
        self.lib = lib
        self.index = index
        self.calls = []
        self.center = 0
        self.stale_center = None
        self.gain_mode_manual = False
        self.t = 0
        self.rng = np.random.default_rng(index)

    def _log(self, *a):
        self.calls.append(a)

    def set_sample_rate(self, hz):
        self._log("set_sample_rate", hz)

    def set_center_freq(self, hz):
        self._log("set_center_freq", hz)
        self.stale_center = self.center
        self.center = hz

    def set_ppm(self, ppm):
        self._log("set_ppm", ppm)

    def tuner_gains_tenth_db(self):
        return [0, 9, 14, 27, 37, 77, 87, 125, 144, 157, 166, 197, 207, 229, 254, 280, 297, 328, 338, 364, 372,
                386, 402, 421, 434, 439, 445, 480, 496]

    def set_manual_gain(self, g):
        self._log("set_manual_gain", g)

    def set_auto_gain(self):
        self._log("set_auto_gain")

    def reset_buffer(self):
        self._log("reset_buffer")

    def usb_strings(self):
        return self.lib.strings[self.index]

    def tuner_type(self):
        return 5

    def close(self):
        self._log("close")

    def read(self, nbytes):
        self._log("read", nbytes)
        if nbytes % dll.READ_BLOCK:
            raise AssertionError("読みの大きさが 16384 の倍数でない")
        if self.lib.fail_read:
            raise DeviceDisconnected("rtlsdr_read_sync が -4（LIBUSB_ERROR_NO_DEVICE）を返した")
        n = nbytes // 2
        t = self.t + np.arange(n)
        self.t += n
        z = (self.rng.normal(size=n) + 1j * self.rng.normal(size=n)) * self.lib.noise
        z = z + self.lib.dc  # 中心の DC の突起
        for f, a in self.lib.tones:
            if self.lib.passband is not None:
                # 合成の通過帯域の落ち込み（電力の dB → 振幅）
                a = a * 10 ** (float(self.lib.passband.db(f - self.center)) / 20)
            z = z + a * np.exp(2j * np.pi * (f - self.center) * t / FS)
        if self.stale_center is not None and self.calls[-2][0] == "reset_buffer":
            # 中心を変えた直後の最初の塊は、古い周波数の強い信号（捨てないとレベルが狂う）
            z[: dll.READ_BLOCK // 2] = 0.9
            self.stale_center = None
        i = np.clip(np.round(z.real * 127.5 + 127.5), 0, 255)
        q = np.clip(np.round(z.imag * 127.5 + 127.5), 0, 255)
        out = np.empty(nbytes, dtype=np.uint8)
        out[0::2], out[1::2] = i, q
        return out


class FakeLib:
    def __init__(self, serials=("SHARED01",)):
        self.strings = [("Realtek", "RTL2838UHIDIR", s) for s in serials]
        self.opened = set()
        self.handles = []
        self.tones = []
        self.noise = 1e-3
        self.dc = 0.02
        self.fail_read = False
        self.passband = None  # sp.Passband なら CW にチューナーの通過帯域の形を掛ける

    def device_count(self):
        return len(self.strings)

    def device_name(self, i):
        return "Generic RTL2832U OEM"

    def device_usb_strings(self, i):
        if i in self.opened:
            raise dll.RtlSdrError("get_device_usb_strings", -3)
        return self.strings[i]

    def open(self, i):
        if i in self.opened:
            raise dll.RtlSdrError("open", -3)
        self.opened.add(i)
        h = FakeHandle(self, i)
        self.handles.append(h)
        return h


def db(x):
    return 10 * np.log10(x)


class Base(unittest.TestCase):
    def setUp(self):
        self.lib = FakeLib()
        self.profile = driver.default_profile()
        # 偽物の機器の通過帯域は平ら（TestPassband 以外）。割り戻しの表を外して、CW のレベルをそのまま比べる
        del self.profile["fft"]["passband"]
        self.model = SweepModel(self.profile, driver.timing_fn(self.profile))
        self.dev = driver.RtlSdrDevice(self.profile, lib=self.lib)

    def sweep(self, start, stop, rbw):
        return self.model.apply(
            SweepRequest(start_hz=start, stop_hz=stop, edited=("start_hz", "stop_hz"), rbw_preference_hz=rbw),
            self.model.default())

    def level_at(self, raw, sweep, f):
        i = (f - sweep.axis.start_hz) // sweep.axis.step_hz
        return db(raw[i])


class TestOpen(Base):
    def test_order_gain_after_first_tune(self):
        ident = self.dev.open(port="rtlsdr:0", ppm=59, gain_db=30)
        self.assertEqual((ident.model, ident.serial, ident.port), ("rtlsdr-r820t", "SHARED01", "rtlsdr:0"))
        names = [c[0] for c in self.lib.handles[0].calls]
        self.assertLess(names.index("set_center_freq"), names.index("set_manual_gain"))
        self.assertIn(("set_ppm", 59), self.lib.handles[0].calls)
        # 30dB に最も近い段は 29.7dB
        self.assertEqual(self.dev.gain_db, 29.7)
        self.assertEqual(len(self.dev.profile()["gain"]["steps_tenth_db"]), 29)

    def test_auto_gain(self):
        self.dev.open(port="rtlsdr:0", gain_db=None)
        self.assertIsNone(self.dev.gain_db)
        self.assertIn(("set_auto_gain",), self.lib.handles[0].calls)

    def test_discover_shared_serial(self):
        lib = FakeLib(serials=("SHARED01", "SHARED01", "ABC"))
        rows = driver.discover(lib=lib)
        self.assertEqual([r.port for r in rows], ["rtlsdr:0", "rtlsdr:1", "rtlsdr:2"])
        self.assertEqual([r.shared_serial for r in rows], [2, 2, 1])
        dev = driver.RtlSdrDevice(lib=lib)
        with self.assertRaises(DeviceError):
            dev.open(serial="SHARED01")
        self.assertEqual(dev.open(serial="ABC").port, "rtlsdr:2")
        dev.close()
        self.assertEqual(driver.RtlSdrDevice(lib=lib).open(port="rtlsdr:1").port, "rtlsdr:1")

    def test_open_missing(self):
        with self.assertRaises(DeviceDisconnected):
            self.dev.open(port="rtlsdr:5")
        with self.assertRaises(DeviceDisconnected):
            self.dev.open(serial="nope")


class TestMeasure(Base):
    def setUp(self):
        super().setUp()
        self.dev.open(port="rtlsdr:0", gain_db=30)
        self.h = self.lib.handles[0]

    def test_stitched_levels_and_peaks(self):
        # FM 帯の広さ（継ぎ合わせ）。0.3（−10.5dBFS）と 0.05（−26dBFS）の CW を格子の上に置く
        self.lib.tones = [(80_000_000, 0.3), (84_700_000, 0.05)]
        s = self.sweep(76 * MHZ, 95 * MHZ, 1500)
        r = self.dev.measure(s)
        self.assertEqual(r.encoding, levels.ENCODING)
        self.assertEqual(r.raw.shape, (s.axis.points,))
        self.assertFalse(np.isnan(r.raw).any())
        self.assertEqual(r.extra["positions"], 25)
        self.assertFalse(r.extra["realtime"])
        self.assertAlmostEqual(self.level_at(r.raw, s, 80_000_000), db(0.09), delta=0.2)
        self.assertAlmostEqual(self.level_at(r.raw, s, 84_700_000), db(0.0025), delta=0.2)
        # 中心を変えるたびに初期化して最初の塊を捨てた（古い周波数の塊が値に入っていない）
        reads = [c for c in self.h.calls if c[0] == "read"]
        self.assertTrue(all(c[1] == dll.READ_BLOCK + 32768 for c in reads))
        # DC の突起（0.02 → −34dBFS）はどの位置の中心にも出ない: 雑音の近く
        for c in r.extra["centers_hz"]:
            if s.axis.start_hz + 10_000 < c < s.axis.last_hz - 10_000:
                self.assertLess(self.level_at(r.raw, s, c), -50)
        self.assertFalse(r.extra["overload"])
        lv, unit = levels.to_level(r.raw, r.extra["gain_db"])
        self.assertEqual(unit, levels.UNIT_UNCALIBRATED)
        self.assertAlmostEqual(float(lv[(80_000_000 - s.axis.start_hz) // 1000]), db(0.09) - 29.7, delta=0.2)

    def test_realtime_center_and_no_retune(self):
        self.lib.tones = [(84_700_000, 0.1)]
        s = self.sweep(84_200_000, 85_000_000, 188)
        r1 = self.dev.measure(s)
        self.assertTrue(r1.extra["realtime"])
        self.assertEqual(r1.extra["centers_hz"], [84_200_000 - 5000])
        self.assertEqual(r1.extra["retunes"], 1)
        r2 = self.dev.measure(s)
        self.assertEqual(r2.extra["retunes"], 0)
        for r in (r1, r2):
            self.assertFalse(np.isnan(r.raw).any())
            self.assertAlmostEqual(self.level_at(r.raw, s, 84_700_000), db(0.01), delta=0.2)
            self.assertEqual(int(np.argmax(r.raw)), (84_700_000 - 84_200_000) // 125)

    def test_overload(self):
        self.lib.tones = [(100_100_000, 1.3)]
        s = self.sweep(100 * MHZ, 100_500_000, 1500)
        r = self.dev.measure(s)
        self.assertTrue(r.extra["overload"])

    def test_fixed_center_marks_dc(self):
        self.dev.fixed_center_hz = 100_250_000
        s = self.sweep(100 * MHZ, 100_500_000, 1500)
        r = self.dev.measure(s)
        f = s.axis.freqs()
        nv = r.extra["flags"]["no_value"]
        np.testing.assert_array_equal(nv, np.abs(f - 100_250_000) < 5000)
        self.assertTrue(np.isnan(r.raw[nv]).all())

    def test_cancel_between_positions(self):
        ev = threading.Event()
        s = self.sweep(76 * MHZ, 95 * MHZ, 1500)
        orig = self.h.read

        def read(n):
            ev.set()
            return orig(n)

        self.h.read = read
        with self.assertRaises(SweepDiscarded) as cm:
            self.dev.measure(s, ev)
        self.assertEqual(cm.exception.reason, "cancelled")
        self.assertEqual(sum(1 for c in self.h.calls if c[0] == "set_center_freq"), 2)  # 開いたとき＋1位置

    def test_disconnect(self):
        self.lib.fail_read = True
        s = self.sweep(100 * MHZ, 100_500_000, 1500)
        with self.assertRaises(DeviceDisconnected):
            self.dev.measure(s)
        self.assertFalse(self.dev.is_open)
        with self.assertRaises(DeviceDisconnected):
            self.dev.measure(s)

    def test_short_read_discarded(self):
        self.h.read = lambda n: np.zeros(100, dtype=np.uint8)
        s = self.sweep(100 * MHZ, 100_500_000, 1500)
        with self.assertRaises(SweepDiscarded) as cm:
            self.dev.measure(s)
        self.assertEqual(cm.exception.reason, "short_read")

    def test_gain_change_during_measure_applies_next(self):
        s = self.sweep(100 * MHZ, 103 * MHZ, 1500)
        orig = self.h.read

        def read(n):
            self.dev.set_gain(40)  # 測っている間: 次の measure の始めに効かせる
            return orig(n)

        self.h.read = read
        r = self.dev.measure(s)
        self.assertEqual(r.extra["gain_db"], 29.7)
        self.h.read = orig
        r = self.dev.measure(s)
        self.assertEqual(r.extra["gain_db"], 40.2)

    def test_step_mismatch_rejected(self):
        s = self.sweep(100 * MHZ, 100_500_000, 1500)
        from dataclasses import replace
        from pygigast.core.types import FreqAxis
        bad = replace(s, axis=FreqAxis(s.axis.start_hz, 999, s.axis.points))
        with self.assertRaises(InvalidSweep):
            self.dev.measure(bad)


class TestLevels(unittest.TestCase):
    def test_units(self):
        raw = np.array([1.0, 0.01, np.nan])
        v, u = levels.to_level(raw, None)
        self.assertEqual(u, levels.UNIT_DBFS)
        np.testing.assert_allclose(v[:2], [0, -20])
        self.assertTrue(np.isnan(v[2]))
        v, u = levels.to_level(raw, 29.7)
        self.assertEqual(u, levels.UNIT_UNCALIBRATED)
        np.testing.assert_allclose(v[:2], [-29.7, -49.7])
        v, u = levels.to_level(raw, 29.7, -12.0)
        self.assertEqual(u, levels.UNIT_DBM)
        np.testing.assert_allclose(v[:2], [-41.7, -61.7])
        self.assertEqual(levels.offset_for_gain({"297": -12.0}, 297), -12.0)
        self.assertIsNone(levels.offset_for_gain({}, 297))
        np.testing.assert_allclose(levels.to_linear(np.array([30.0])), [1.0])


# 合成の通過帯域（実機より強い落ち込み・上下で違う形）
SYN_PASSBAND = {"offset_hz": [0, 300_000, 600_000, 820_000], "upper_db": [0.0, -0.5, -1.0, -3.0],
                "lower_db": [0.0, 0.4, -1.5, -4.0]}


class TestPassband(Base):
    """チューナーの通過帯域の形を割り戻す（設計Q §6.9）。偽物の機器に合成の落ち込みを掛けて確かめる。"""

    def setUp(self):
        super().setUp()
        self.lib.passband = sp.Passband(**SYN_PASSBAND)
        self.lib.noise = 1e-4

    def device(self, corrected):
        p = driver.default_profile()
        if corrected:
            p["fft"]["passband"] = dict(SYN_PASSBAND)
        else:
            del p["fft"]["passband"]
        # 開いた機器ごとに偽物の DLL を作り直す（偽物は閉じても「開いている」のまま）
        lib = FakeLib()
        lib.tones, lib.noise, lib.passband = self.lib.tones, self.lib.noise, self.lib.passband
        return driver.RtlSdrDevice(p, lib=lib)

    def test_cw_taken_from_band_edge_is_restored(self):
        s = self.sweep(76 * MHZ, 80 * MHZ, 1500)
        c = driver.make_plan(self.profile, s.axis, s.rbw_hz)[1].centers
        # 中心ちょうど（δ 未満）の点は隣の位置の端（約 810kHz 離れた所）から採る。中心の間の点は約 400kHz
        tones = [(c[2], 0.1), (c[3], 0.1), ((c[4] + c[5]) // 2, 0.1)]
        self.lib.tones = tones
        for corrected in (True, False):
            dev = self.device(corrected)
            dev.open(port="rtlsdr:0", gain_db=30)
            r = dev.measure(s)
            dev.close()
            self.assertIs(r.extra["passband_corrected"], corrected)
            for f, a in tones:
                got = self.level_at(r.raw, s, f)
                if corrected:
                    self.assertAlmostEqual(got, db(a * a), delta=0.1, msg=f)
                else:
                    self.assertLess(got, db(a * a) - 0.5, msg=f)

    def test_realtime_cw_across_the_band(self):
        # リアルタイム（中心 = Start − δ）: 中心から 100〜800kHz の CW がどれも同じレベルに戻る
        s = self.sweep(84_200_000, 85_000_000, 1500)
        centre = 84_200_000 - 5000
        tones = [(centre + o, 0.1) for o in (105_000, 405_000, 605_000, 805_000)]
        self.lib.tones = tones
        dev = self.device(True)
        dev.open(port="rtlsdr:0", gain_db=30)
        r = dev.measure(s)
        self.assertEqual(r.extra["centers_hz"], [centre])
        for f, a in tones:
            self.assertAlmostEqual(self.level_at(r.raw, s, f), db(a * a), delta=0.1, msg=f)

    def test_default_profile_keeps_cw_near_centre(self):
        # 実機で測った表は中心の近くで 0dB: 中心の近くの CW のレベルは割り戻しても変わらない
        pb = sp.Passband.from_profile(driver.default_profile())
        off = np.arange(-50_000, 50_001, 1000)
        self.assertLess(float(np.max(np.abs(pb.db(off)))), 0.05)


if __name__ == "__main__":
    unittest.main()
