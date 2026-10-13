"""計測器の本体に RTL-SDR をつないだときの試験（設計Q §6）。DLL の代わりに合成の IQ を返す偽物を使う。

すべて一時フォルダの中で行う（設定・校正・記録は一時フォルダ）。
"""
from __future__ import annotations

import socket
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path

import numpy as np

from namibench.api.local import _RtlSdrBackend, _SimulatorBackend
from namibench.core.devices.base import load_profile
from namibench.core.devices.rtlsdr import dll
from namibench.core.instrument import GIGAST_PROFILE_PATH, Instrument
from namibench.core.instrument_common import CommandError
from namibench.core.instrument_rtlsdr import RtlSdrCalibrationStore
from namibench.core.devices.rtlsdr import levels as rtl_levels
from namibench.core.storage.h5file import H5Record

FS = 2_048_000
MHZ = 1_000_000
UNCAL = "dB（未校正）"
GAINS = [0, 9, 14, 27, 37, 77, 87, 125, 144, 157, 166, 197, 207, 229, 254, 280, 297, 328, 338, 364, 372,
         386, 402, 421, 434, 439, 445, 480, 496]


class FakeHandle:
    """開いた機器の偽物。中心周波数に対する相対の周波数で、位相の続く合成の IQ を作る。"""

    def __init__(self, lib, index):
        self.lib = lib
        self.index = index
        self.calls = []
        self.center = 0
        self.t = 0
        self.rng = np.random.default_rng(index)

    def set_sample_rate(self, hz):
        self.calls.append(("set_sample_rate", hz))

    def set_center_freq(self, hz):
        self.calls.append(("set_center_freq", hz))
        self.center = hz

    def set_ppm(self, ppm):
        self.calls.append(("set_ppm", ppm))

    def tuner_gains_tenth_db(self):
        return list(GAINS)

    def set_manual_gain(self, g):
        self.calls.append(("set_manual_gain", g))

    def set_auto_gain(self):
        self.calls.append(("set_auto_gain",))

    def reset_buffer(self):
        self.calls.append(("reset_buffer",))

    def usb_strings(self):
        return self.lib.strings[self.index]

    def tuner_type(self):
        return 5

    def close(self):
        self.calls.append(("close",))
        self.lib.opened.discard(self.index)

    def read(self, nbytes):
        if nbytes % dll.READ_BLOCK:
            raise AssertionError("読みの大きさが 16384 の倍数でない")
        n = nbytes // 2
        t = self.t + np.arange(n)
        self.t += n
        z = (self.rng.normal(size=n) + 1j * self.rng.normal(size=n)) * self.lib.noise + 0.02
        for f, a in self.lib.tones:
            z = z + a * np.exp(2j * np.pi * (f - self.center) * t / FS)
        out = np.empty(nbytes, dtype=np.uint8)
        out[0::2] = np.clip(np.round(z.real * 127.5 + 127.5), 0, 255)
        out[1::2] = np.clip(np.round(z.imag * 127.5 + 127.5), 0, 255)
        return out


class FakeLib:
    def __init__(self, serials=("00000001",)):
        self.strings = [("Realtek", "RTL2838UHIDIR", s) for s in serials]
        self.opened = set()
        self.handles = []
        self.tones = []
        self.noise = 1e-3

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


def free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def recv_exact(sock: socket.socket, n: int, timeout: float = 10.0) -> bytes:
    sock.settimeout(timeout)
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return buf


class RtlSdrCase(unittest.TestCase):
    """RTL-SDR（偽物の DLL）とシミュレータの2つを持つ本体。"""

    serials = ("00000001",)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.lib = FakeLib(self.serials)
        self.lib_factory = lambda: self.lib
        self.profile = load_profile(GIGAST_PROFILE_PATH)
        self.inst = self.make_instrument()

    def make_instrument(self):
        sim = _SimulatorBackend(self.profile)
        sim.speed = 0.05
        rtl = _RtlSdrBackend(lib_factory=lambda: self.lib_factory())
        inst = Instrument({"simulator": sim, "rtlsdr": rtl}, profile=self.profile,
                          data_root=self.root / "data", cal_root=self.root / "cal")
        self.events = []
        self.cv = threading.Condition()

        def on_event(kind, payload):
            with self.cv:
                self.events.append((kind, payload))
                self.cv.notify_all()

        inst.subscribe(on_event)
        return inst

    def tearDown(self):
        self.inst.shutdown()
        self.tmp.cleanup()

    def call(self, name, /, **kw):
        return self.inst.submit(name, **kw).result(30)

    def clear_events(self):
        with self.cv:
            self.events.clear()

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

    def notices(self, code):
        with self.cv:
            return [p for k, p in self.events if k == "notice" and p["code"] == code]

    def connect_rtl(self, port="rtlsdr:0", start=84 * MHZ, stop=85_500_000):
        self.call("connect", backend="rtlsdr", port=port)
        self.call("set_sweep", start_hz=start, stop_hz=stop)

    def single(self):
        self.clear_events()
        self.call("run", mode="single")
        self.wait_for(lambda k, p: k == "state" and p["run"]["state"] == "stopped"
                      and p["run"]["last_stop_reason"] == "single_done")
        return self.call("get_trace", id=1)


class TestConnectAndLevels(RtlSdrCase):
    def test_profile_follows_device_and_unit_is_uncalibrated(self):
        self.lib.tones = [(84_700_000, 0.05)]
        self.call("marker_add", kind="M", trace=1, freq_hz=84_700_000)
        self.call("connect", backend="rtlsdr", port="rtlsdr:0")
        st = self.call("get_state")
        self.assertEqual(st["connection"]["profile_model"], "rtlsdr-r820t")
        self.assertEqual(st["connection"]["sweep_kind"], "fft")
        # GigaSt の範囲いっぱい（RTL-SDR の範囲の外）は機種の既定の範囲へ（設計Q §6.9）
        self.assertEqual((st["sweep"]["start_hz"], st["sweep"]["stop_hz"]), (76 * MHZ, 95 * MHZ))
        n = self.notices("device_profile_changed")[0]
        self.assertEqual(n["detail"]["model"], "rtlsdr-r820t")
        self.assertIn("device_default_range", [a["reason"] for a in n["detail"]["adjustments"]])
        # マーカーは残す
        self.assertEqual(len(st["markers"]["markers"]), 1)
        self.assertEqual(st["rtlsdr"]["gain_steps_db"][-1], 49.6)
        self.assertEqual(st["rtlsdr"]["gain_db_actual"], 29.7)
        self.call("set_sweep", start_hz=84 * MHZ, stop_hz=85_500_000)
        tr = self.single()
        self.assertEqual(tr["unit"], UNCAL)
        self.assertEqual(self.call("get_state")["traces"][0]["unit"], UNCAL)
        v = np.asarray(tr["values"])
        self.assertTrue(np.isfinite(v).all())
        i = int(np.nanargmax(v))
        self.assertEqual(tr["axis"]["start_hz"] + i * tr["axis"]["step_hz"], 84_700_000)
        # dBFS − 利得: 振幅 0.05 の CW は −26dBFS、利得 29.7dB で約 −55.7
        self.assertAlmostEqual(float(v[i]), 20 * np.log10(0.05) - 29.7, delta=1.0)
        row = self.call("get_state")["markers"]["markers"][0]
        self.assertEqual(row["unit"], UNCAL)

    def test_switch_back_to_simulator(self):
        self.connect_rtl()
        self.call("connect", backend="simulator", serial=_SimulatorBackend.SERIAL)
        st = self.call("get_state")
        self.assertEqual(st["connection"]["profile_model"], self.profile["model"])
        self.assertEqual(st["connection"]["sweep_kind"], "stepped")
        # RTL-SDR の範囲は GigaSt の範囲に収まるので残る。RBW は GigaSt の選択肢へ
        self.assertEqual((st["sweep"]["start_hz"], st["sweep"]["stop_hz"]), (84 * MHZ, 85_500_000))
        self.assertIn(st["sweep"]["rbw_hz"], self.profile["rbw"]["choices_hz"])

    def test_gain_setting_and_agc(self):
        self.connect_rtl()
        res = self.call("rtlsdr_set", gain_db=40.0)
        self.assertEqual(res["rtlsdr"]["gain_db"], 40.0)
        self.assertEqual(res["rtlsdr"]["gain_db_actual"], 40.2)
        tr = self.single()
        self.assertEqual(tr["unit"], UNCAL)
        res = self.call("rtlsdr_set", agc=True)
        self.assertTrue(res["rtlsdr"]["agc"])
        tr = self.single()
        self.assertEqual(tr["unit"], "dBFS")  # AGC の間は利得が分からない（設計Q §6.9）
        with self.assertRaises(CommandError) as cm:
            self.call("rtlsdr_set", gain_db="x")
        self.assertEqual(cm.exception.code, "bad_value")
        # 設定に残る
        self.inst.shutdown()
        self.inst = self.make_instrument()
        self.assertTrue(self.call("get_state")["rtlsdr"]["agc"])

    def test_overload_notice_once_per_condition(self):
        self.lib.tones = [(84_700_000, 1.5)]
        self.connect_rtl()
        self.call("run", mode="single", count=3)
        self.wait_for(lambda k, p: k == "state" and p["run"]["last_stop_reason"] == "single_done")
        self.assertEqual(len(self.notices("rtlsdr_overload")), 1)
        self.assertTrue(self.call("get_state")["rtlsdr"]["overload"])

    def wait_task(self, task_id):
        n = self.wait_for(lambda k, p: k == "notice" and p["code"].startswith("task_")
                          and p["detail"].get("task_id") == task_id)
        self.assertEqual(n["code"], "task_done", n)
        return n

    def test_save_keeps_raw_and_levels_rebuild(self):
        """1回分の保存に RTL-SDR の生の値（float32）と換算に要る利得を持つ（設計Q §6.1・設計H §7.1）。"""
        self.lib.tones = [(84_700_000, 0.05)]
        self.connect_rtl()
        tr = self.single()
        self.clear_events()
        path = self.root / "a.h5"
        self.wait_task(self.call("save", path=str(path))["task_id"])
        with H5Record(path) as h:
            s = h.segments[0]
            self.assertEqual(s.raw_encoding, rtl_levels.ENCODING)
            self.assertEqual(h.read(0).raw.dtype, np.float32)
            r = h.raw_sweep(0, 0)
            self.assertEqual(r.extra["gain_db"], s.raw_extra["gain_db"])
            self.assertEqual(r.extra["window"], "hann")
            # 読み戻した生の値から、画面のトレースと同じレベル（dB（未校正））を作り直せる
            level, unit = rtl_levels.to_level(r.raw, r.extra["gain_db"])
        self.assertEqual(unit, UNCAL)
        live = np.array([np.nan if v is None else v for v in tr["values"]], dtype=np.float64)
        n = min(live.size, level.size)
        np.testing.assert_allclose(level[:n], live[:n], atol=1e-3, equal_nan=True)
        opened = self.call("open", paths=[str(path)])
        self.wait_task(opened["task_id"])
        f = self.call("get_state")["files"][0]
        self.assertEqual(f["traces"][0]["unit"], UNCAL)

    def test_record_finish_and_open(self):
        """RTL-SDR も記録でき、止めて HDF5 へまとめ、開ける（設計Q §6.1・設計H §4・§7.2）。"""
        self.lib.tones = [(84_700_000, 0.05)]
        self.connect_rtl()
        self.call("record_start")
        self.clear_events()
        self.call("run", mode="continuous")
        self.wait_for(lambda k, p: k == "state" and p["recording"].get("sweeps", 0) >= 3)
        self.call("stop")
        path = self.root / "rec.h5"
        self.wait_task(self.call("record_finish", path=str(path))["task_id"])
        self.assertEqual(self.notices("record_not_supported_for_device"), [])
        self.assertEqual(self.notices("record_error"), [])
        with H5Record(path) as h:
            self.assertEqual(len(h.segments), 1)
            s = h.segments[0]
            self.assertGreaterEqual(s.n_sweeps, 3)
            self.assertEqual(s.raw_encoding, rtl_levels.ENCODING)
            self.assertEqual(s.raw_extra["gain_mode"], "manual")
            self.assertEqual(h.read(0).raw.dtype, np.float32)
            self.assertEqual(h.overview(0, 4).values.dtype, np.float32)
            r = h.raw_sweep(0, s.n_sweeps - 1)
            self.assertEqual(r.encoding, rtl_levels.ENCODING)
            self.assertIsNotNone(r.extra["gain_db"])
            peak = int(np.nanargmax(r.raw))
            self.assertEqual(int(r.adopted.axis.freqs()[peak]), 84_700_000)
            # 根の校正は RTL-SDR の個体の校正（無ければ null）。GigaSt の校正を写さない
            self.assertNotEqual((h.header.calibration or {}).get("format"), "pygigast-calibration")
        # 記録のファイル（掃引が2つ以上）は再生する（設計H §4・設計P §5）。RTL-SDR の記録も記録した換算で出す
        opened = self.call("open", paths=[str(path)])
        self.wait_task(opened["task_id"])
        st = self.call("get_state")
        self.assertTrue(st["playback"]["active"])
        self.assertEqual(st["playback"]["problems"], [])
        self.clear_events()
        self.call("playback_seek", index=st["playback"]["total"] - 1)
        tr = self.wait_for(lambda k, p: k == "trace" and p.get("trace") == 1)
        self.assertEqual(int(np.nanargmax(tr["values"])), peak)


class TestHannNote(RtlSdrCase):
    """Hann 窓で測ったトレースのマーカーの読み値に注意を添える（設計Q §6.4・設計G §3.3）。"""

    def marker_notes(self):
        rows = self.call("get_state")["markers"]["markers"]
        return rows[0]["notes"]

    def test_hann_note_on_marker(self):
        self.lib.tones = [(84_700_000, 0.05)]
        self.connect_rtl()
        self.single()
        self.call("marker_add", kind="M", trace=1, freq_hz=84_700_000)
        self.assertIn("hann_scalloping", self.marker_notes())

    def test_no_note_with_flat_top(self):
        self.lib.tones = [(84_700_000, 0.05)]
        self.connect_rtl()
        self.call("set_sweep", rbw_hz=1885)  # Flat top の RBW（profile.json の rbw.choices）
        self.assertEqual(self.call("get_state")["sweep"]["rbw_hz"], 1885)
        self.single()
        self.call("marker_add", kind="M", trace=1, freq_hz=84_700_000)
        self.assertNotIn("hann_scalloping", self.marker_notes())

    def test_no_note_on_gigast(self):
        self.call("connect", backend="simulator", serial=_SimulatorBackend.SERIAL)
        self.call("set_sweep", start_hz=50_000_000, stop_hz=200_000_000)
        self.single()
        self.call("marker_add", kind="M", trace=1)
        self.assertNotIn("hann_scalloping", self.marker_notes())


class TestCalibration(RtlSdrCase):
    def test_ppm_from_marker(self):
        # 本当は 84.700MHz の信号が 84.705MHz に見える（水晶が遅れている）
        self.lib.tones = [(84_705_000, 0.1)]
        self.connect_rtl()
        self.single()
        m = self.call("marker_add", kind="M", trace=1)
        self.assertEqual(m["freq_hz"], 84_705_000)
        res = self.call("rtlsdr_calibrate_ppm", marker_id=m["id"], true_freq_hz=84_700_000)
        self.assertEqual(res["previous_ppm"], 0)
        self.assertEqual(res["ppm"], round((84_700_000 - 84_705_000) / 84_700_000 * 1e6))
        ppm = res["ppm"]
        st = self.call("get_state")
        self.assertEqual(st["rtlsdr"]["calibration"]["ppm"], ppm)
        cal = RtlSdrCalibrationStore(self.root / "cal").load_current("00000001")
        self.assertEqual(cal.ppm, ppm)
        # 次につないだとき（別の本体）に DLL へ渡す
        self.inst.shutdown()
        self.lib.opened.clear()
        self.inst = self.make_instrument()
        self.call("connect", backend="rtlsdr", port="rtlsdr:0")
        self.assertIn(("set_ppm", ppm), self.lib.handles[-1].calls)
        self.assertEqual(self.call("get_state")["rtlsdr"]["ppm"], ppm)

    def test_level_calibration_makes_dbm(self):
        self.lib.tones = [(84_700_000, 0.1)]
        self.connect_rtl()
        self.single()
        m = self.call("marker_add", kind="M", trace=1)
        res = self.call("rtlsdr_calibrate_level", marker_id=m["id"], true_dbm=-50.0)
        self.assertIsNone(res["previous_offset_db"])
        tr = self.single()
        self.assertEqual(tr["unit"], "dBm")
        row = self.call("get_state")["markers"]["markers"][0]
        self.assertEqual(row["unit"], "dBm")
        self.assertAlmostEqual(row["level"], -50.0, delta=0.5)

    def test_ppm_needs_rtlsdr(self):
        with self.assertRaises(CommandError) as cm:
            self.call("rtlsdr_set", ppm=10)
        self.assertEqual(cm.exception.code, "rtlsdr_not_connected")


class TestDiscovery(RtlSdrCase):
    serials = ("00000001", "00000001")

    def test_shared_serial(self):
        devs = self.call("list_devices", backend="rtlsdr")["devices"]
        self.assertEqual([d["port"] for d in devs], ["rtlsdr:0", "rtlsdr:1"])
        self.assertEqual([d["shared_serial"] for d in devs], [2, 2])
        with self.assertRaises(CommandError) as cm:
            self.call("connect", backend="rtlsdr", serial="00000001")
        self.assertEqual(cm.exception.code, "device_ambiguous")
        self.call("connect", backend="rtlsdr", port="rtlsdr:1")
        n = self.notices("rtlsdr_shared_serial")[0]["detail"]
        self.assertEqual((n["count"], n["calibration_shared"]), (2, True))
        self.call("disconnect")
        end = time.monotonic() + 10
        while self.lib.opened and time.monotonic() < end:  # 機器スレッドが閉じるのを待つ
            time.sleep(0.01)
        # 同じシリアル番号が複数あるときは自動では繋がない（設計Q §6.6）
        self.assertIsNone(self.call("connect_last", backend="rtlsdr")["identity"])


class TestDllNotFound(RtlSdrCase):
    def test_dll_not_found_keeps_gigast(self):
        def missing():
            raise dll.DllNotFound("RTL-SDR の DLL が見つからない")

        self.lib_factory = missing
        with self.assertRaises(CommandError) as cm:
            self.call("list_devices", backend="rtlsdr")
        self.assertEqual(cm.exception.code, "rtlsdr_dll_not_found")
        with self.assertRaises(CommandError) as cm:
            self.call("connect", backend="rtlsdr", port="rtlsdr:0")
        self.assertEqual(cm.exception.code, "rtlsdr_dll_not_found")
        self.call("connect", backend="simulator", serial=_SimulatorBackend.SERIAL)
        self.assertEqual(self.call("get_state")["connection"]["state"], "connected")

    def test_dll_folder_setting(self):
        res = self.call("rtlsdr_set", dll_folder=str(self.root))
        self.assertEqual(res["rtlsdr"]["dll_folder"], str(self.root))
        self.inst.shutdown()
        self.inst = self.make_instrument()
        self.assertEqual(self.call("get_state")["rtlsdr"]["dll_folder"], str(self.root))


class TestRtlTcp(RtlSdrCase):
    def test_client_header_iq_and_fixed_center(self):
        self.lib.tones = [(84_700_000, 0.05)]
        port = free_port()
        self.call("rtltcp_set", enabled=True, port=port, bind="local")
        self.connect_rtl(start=84 * MHZ, stop=85 * MHZ)
        self.call("run", mode="continuous")
        with socket.create_connection(("127.0.0.1", port), timeout=5) as c:
            hdr = recv_exact(c, 12)
            self.assertEqual(hdr[:4], b"RTL0")
            self.assertEqual(struct.unpack(">II", hdr[4:]), (5, len(GAINS)))
            data = recv_exact(c, 65536)
            self.assertEqual(len(data), 65536)
            # 読み取った IQ のまま（DC 0.02 の分だけ 128 のまわり）
            self.assertTrue(120 < np.frombuffer(data, dtype=np.uint8).mean() < 136)
            st = self.wait_for(lambda k, p: k == "state" and p["rtlsdr"]["fixed_center"] is not None)
            self.assertEqual(st["rtlsdr"]["fixed_center"]["cause"], "rtltcp_clients")
            # 周波数の命令: その周波数を中心にしたリアルタイムの動き
            self.clear_events()
            c.sendall(struct.pack(">BI", 0x01, 84_600_000))
            n = self.wait_for(lambda k, p: k == "notice" and p["code"] == "rtltcp_freq_from_client")
            self.assertTrue(n["detail"]["applied"])
            st = self.call("get_state")
            self.assertEqual(st["sweep"]["center_hz"], 84_600_000)
            self.assertEqual(st["rtlsdr"]["fixed_center"]["tuner_center_hz"], 84_600_000)
            # 値の無い点（中心の DC）は NaN と印（設計Q §6.4）
            self.clear_events()
            tr = self.wait_for(lambda k, p: k == "trace" and p["display"]["start_hz"] == st["sweep"]["start_hz"])
            v = np.asarray(tr["values"])
            nv = np.asarray(tr["flags"]["points"]["no_value"])
            i = (84_600_000 - tr["axis"]["start_hz"]) // tr["axis"]["step_hz"]
            self.assertTrue(nv[i])
            self.assertTrue(np.isnan(v[i]))
            self.assertTrue(np.isnan(v[nv]).all())
            self.assertTrue(np.isfinite(v[~nv]).all())
            # 継ぎ合わせの要る広さは受け付けない（設計Q §6.7）
            with self.assertRaises(CommandError) as cm:
                self.call("set_sweep", start_hz=80 * MHZ, stop_hz=90 * MHZ)
            self.assertEqual(cm.exception.code, "rtlsdr_stitching_blocked")
            self.assertEqual(cm.exception.detail["cause"], "rtltcp_clients")
            # 利得の命令に従う
            c.sendall(struct.pack(">BI", 0x04, 402))
            self.wait_for(lambda k, p: k == "notice" and p["code"] == "rtltcp_gain_from_client")
            self.assertEqual(self.call("get_state")["rtlsdr"]["gain_db"], 40.2)
            # 測っている間は次の測定の始めに効く
            self.wait_for(lambda k, p: k == "state" and p["rtlsdr"]["gain_db_actual"] == 40.2)
        # 相手がいなくなったら中心を動かしてよい
        self.wait_for(lambda k, p: k == "state" and p["rtltcp"]["clients"] == 0
                      and p["rtlsdr"]["fixed_center"] is None)
        self.call("set_sweep", start_hz=80 * MHZ, stop_hz=90 * MHZ)
        self.call("stop")

    def test_gigast_header_unchanged(self):
        port = free_port()
        self.call("rtltcp_set", enabled=True, port=port, bind="local")
        with socket.create_connection(("127.0.0.1", port), timeout=5) as c:
            self.assertEqual(recv_exact(c, 12), b"RTL0" + struct.pack(">II", 0, 0))


if __name__ == "__main__":
    unittest.main()
