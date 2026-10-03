"""機器の一覧・名前・使用中の見分け・自動接続（設計J §4.7・設計B §7.3）と、上段の「基準を取る」の
REF レベル（設計E §6.2・J §4.2）を、偽の GigaSt の種類・偽の DLL の RTL-SDR・シミュレータで確かめる。"""
import tempfile
import threading
import time
import unittest
from pathlib import Path

import serial

from pygigast.api.local import _RtlSdrBackend, _SimulatorBackend
from pygigast.core.devices.base import load_profile
from pygigast.core.devices.gigast.transport import is_port_in_use
from pygigast.core.devices.rtlsdr import dll
from pygigast.core.instrument import GIGAST_PROFILE_PATH, CommandError, Instrument
from pygigast.core.units import ParseError, minus_sign, parse_db
from pygigast.ui_qt import screen_model, texts
from tests.device_helpers import FakeGigaBackend
from tests.instrument_helpers import InstrumentCase
from tests.test_instrument_rtlsdr import FakeLib

SERIAL = "FT00AB12"


class DevicesCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.profile = load_profile(GIGAST_PROFILE_PATH)
        self.giga = FakeGigaBackend(self.profile)
        self.lib = FakeLib(("00000001",))
        self.lib_factory = lambda: self.lib
        self.inst = self.make_instrument()

    def make_instrument(self):
        sim = _SimulatorBackend(self.profile)
        sim.speed = 0.05
        rtl = _RtlSdrBackend(lib_factory=lambda: self.lib_factory())
        inst = Instrument({"gigast": self.giga, "simulator": sim, "rtlsdr": rtl}, profile=self.profile,
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
        return self.inst.submit(name, **kw).result(20)

    def rows(self):
        return {d["id"]: d for d in self.call("list_devices")["devices"]}

    def wait_devices(self, pred, timeout=5.0):
        end = time.monotonic() + timeout
        with self.cv:
            while True:
                for k, p in self.events:
                    if k == "devices" and pred(p["devices"]):
                        return p["devices"]
                left = end - time.monotonic()
                if left <= 0:
                    self.fail("devices のイベントが来ない")
                self.cv.wait(left)


class TestList(DevicesCase):
    def test_all_kinds_in_one_list_with_states_and_names(self):
        self.giga.plug("COM7", SERIAL)
        rows = self.rows()
        self.assertEqual([d["kind"] for d in rows.values()], ["gigast", "rtlsdr", "simulator"])
        g = rows["gigast:COM7"]
        # 初めての機器は確認の掃引をしてから繋ぐ。既定の名前は機種名＋シリアル番号の末尾4桁
        self.assertEqual(g["state"], "first_time_needs_probe")
        self.assertEqual(g["name"], "GigaSt v5 AB12")
        self.assertEqual(g["usb_id"], "0403:6001")
        self.assertEqual(rows["rtlsdr:rtlsdr:0"]["state"], "available")
        self.assertEqual(rows["rtlsdr:rtlsdr:0"]["name"], "RTL-SDR (R820T) 0001")
        self.call("connect", backend="gigast", port="COM7")
        rows = self.rows()
        self.assertEqual(rows["gigast:COM7"]["state"], "in_use_here")
        self.assertEqual(self.call("get_state")["connection"]["name"], "GigaSt v5 AB12")
        self.call("disconnect")
        self.assertEqual(self.rows()["gigast:COM7"]["state"], "known")

    def test_rtlsdr_open_elsewhere_and_dll_missing(self):
        self.lib.opened.add(0)  # ほかのアプリが開いている
        self.assertEqual(self.rows()["rtlsdr:rtlsdr:0"]["state"], "busy_other_app")
        with self.assertRaises(CommandError) as cm:
            self.call("connect", backend="rtlsdr", port="rtlsdr:0")
        self.assertEqual(cm.exception.code, "port_in_use")

        def missing():
            raise dll.DllNotFound("無い")

        self.lib_factory = missing
        row = self.rows()["rtlsdr:dll"]
        self.assertEqual(row["state"], "dll_not_found")
        # GigaSt・シミュレータの一覧はそのまま出る
        self.assertIn("simulator:simulator", self.rows())

    def test_rename_persists_and_resets(self):
        self.giga.plug("COM7", SERIAL)
        r = self.call("rename_device", kind="gigast", serial=SERIAL, name="  机の GigaSt ")
        self.assertEqual(r["name"], "机の GigaSt")
        self.wait_devices(lambda ds: any(d["name"] == "机の GigaSt" for d in ds))
        self.inst.shutdown()
        self.inst = self.make_instrument()
        self.assertEqual(self.rows()["gigast:COM7"]["name"], "机の GigaSt")
        self.call("rename_device", kind="gigast", serial=SERIAL, name="")
        self.assertEqual(self.rows()["gigast:COM7"]["name"], "GigaSt v5 AB12")
        with self.assertRaises(CommandError):
            self.call("rename_device", kind="nope", serial=SERIAL, name="x")


class TestPolling(DevicesCase):
    def test_plug_while_disconnected_emits_devices(self):
        self.giga.plug("COM7", SERIAL)
        ds = self.wait_devices(lambda ds: any(d["port"] == "COM7" for d in ds))
        self.assertEqual(next(d for d in ds if d["port"] == "COM7")["state"], "first_time_needs_probe")
        self.giga.unplug("COM7")
        self.wait_devices(lambda ds: not any(d["port"] == "COM7" for d in ds))

    def test_no_polling_while_connected_unless_watched(self):
        self.giga.plug("COM7", SERIAL)
        self.call("connect", backend="gigast", port="COM7")
        time.sleep(0.3)
        n = self.giga.discover_calls
        time.sleep(1.2)
        self.assertEqual(self.giga.discover_calls, n)  # 繋いでいて誰も見ていなければ見ない
        self.call("list_devices", watch_s=5)
        with self.cv:
            self.events.clear()
        self.giga.plug("COM9", "FT000009")
        self.wait_devices(lambda ds: any(d["port"] == "COM9" for d in ds))
        with self.assertRaises(CommandError):
            self.call("list_devices", watch_s=-1)


class TestConnectErrors(DevicesCase):
    def test_port_in_use_is_not_probe_failed(self):
        self.giga.plug("COM7", SERIAL)
        self.giga.busy.add("COM7")
        with self.assertRaises(CommandError) as cm:
            self.call("connect", backend="gigast", port="COM7")
        self.assertEqual(cm.exception.code, "port_in_use")
        self.assertEqual(self.rows()["gigast:COM7"]["state"], "busy_other_app")
        # 空いたら繋げる。繋げたら「使用中」の覚えは消える
        self.giga.busy.clear()
        self.call("connect", backend="gigast", port="COM7")
        self.assertEqual(self.rows()["gigast:COM7"]["state"], "in_use_here")

    def test_probe_failed(self):
        self.giga.plug("COM6", "FT111111")
        self.giga.probe_result = False
        with self.assertRaises(CommandError) as cm:
            self.call("connect", backend="gigast", port="COM6")
        self.assertEqual(cm.exception.code, "probe_failed")
        self.assertIn("USB を挿し直して", texts.connect_failed_text("GigaSt v5 1111", texts.ERRORS["probe_failed"],
                                                               "probe_failed"))

    def test_is_port_in_use(self):
        busy = serial.SerialException("could not open port 'COM7': PermissionError(13, 'アクセスが拒否されました。', None, 5)")
        self.assertTrue(is_port_in_use(busy))
        gone = serial.SerialException("could not open port 'COM9': FileNotFoundError(2, '指定されたファイルが見つかりません。', None, 2)")
        self.assertFalse(is_port_in_use(gone))


class TestAutoConnect(DevicesCase):
    def test_last_rtlsdr_is_found_and_announced(self):
        self.call("connect", backend="rtlsdr", port="rtlsdr:0")
        self.inst.shutdown()
        self.lib.opened.clear()
        self.inst = self.make_instrument()
        r = self.call("connect_last")
        self.assertEqual(r["identity"]["serial"], "00000001")
        with self.cv:
            n = [p for k, p in self.events if k == "notice" and p["code"] == "auto_connected"]
        self.assertEqual(n[0]["detail"]["name"], "RTL-SDR (R820T) 0001")
        self.assertEqual(texts.notice_text(n[0]), "前回の RTL-SDR (R820T) 0001 に繋ぎました")

    def test_simulator_is_never_auto_connected(self):
        self.call("connect", backend="simulator", serial="SIM00001")
        self.call("disconnect")
        self.assertIsNone(self.call("connect_last")["identity"])

    def test_dll_missing_still_finds_gigast(self):
        self.giga.plug("COM7", SERIAL)
        self.call("connect", backend="gigast", port="COM7")
        self.call("disconnect")

        def missing():
            raise dll.DllNotFound("無い")

        self.lib_factory = missing
        self.assertEqual(self.call("connect_last")["identity"]["serial"], SERIAL)


class TestTexts(unittest.TestCase):
    def test_status_when_disconnected_and_row_views(self):
        st = {"connection": {"state": "disconnected"}, "run": {}}
        conn = next(i for i in texts.status_items(st) if i["key"] == "conn")
        self.assertEqual(conn["text"], "● 未接続（機器 → 機器に接続… で選べます）")
        v = texts.devices_view([{"id": "simulator:simulator", "kind": "simulator", "serial": "SIM00001",
                                 "state": "available", "name": None, "default_name": None}])
        self.assertTrue(v["empty"])
        self.assertEqual(v["rows"], [])
        self.assertEqual(texts.device_name(v["simulator"]), "シミュレータ")
        row = {"id": "gigast:COM7", "kind": "gigast", "port": "COM7", "serial": SERIAL, "usb_id": "0403:6001",
               "description": "USB Serial Port", "state": "busy_other_app", "name": "GigaSt v5 AB12"}
        v = texts.devices_view([row])
        self.assertFalse(v["empty"])
        rv = v["rows"][0]["view"]
        self.assertEqual(rv["state_text"], "ほかのアプリケーションが使っています")
        self.assertIn("ポート COM7", rv["details"])
        self.assertIn("USB ID 0403:6001", rv["details"])

    def test_minus_sign(self):
        self.assertEqual(minus_sign("-37.0 dBm"), "−37.0 dBm")
        self.assertEqual(parse_db("−37.5 dBm"), -37.5)
        self.assertEqual(parse_db("-37.5"), -37.5)
        with self.assertRaises(ParseError):
            parse_db("−x")


class TestDirectZeroDb(InstrumentCase):
    """上段の「基準を取る」: 取ったら 0dB が見える REF レベルにし、変えたことを結果で知らせる（E §6.2・J §4）。"""

    def take(self, **kw):
        r = self.call("reference_take", kind="wide", name="直結 12:00", show_zero=True, **kw)
        return self.wait_task(r["task_id"])

    def test_ref_moves_only_when_zero_is_out_of_view(self):
        self.connect()
        self.call("set_mode", mode="tg")
        self.call("set_amplitude", ref_level=-50.0, db_per_div=10)
        n = self.take()
        self.assertEqual(n["code"], "task_done")
        self.assertEqual(n["detail"]["result"]["ref_level"], 10.0)
        self.assertEqual(self.call("get_state")["amplitude"]["ref_level"], 10.0)
        self.assertIn("REF レベルを +10 dB", texts.task_end_text("task_done", n["detail"])[0])
        # すぐ1回測って 0dB を見せる（設計E §6.2）
        self.wait_for(lambda k, p: k == "state" and p["run"]["last_stop_reason"] == "single_done")
        st = self.call("get_state")
        q = screen_model.ref_quick_view(st)
        self.assertTrue(q["visible"])
        self.assertTrue(q["text"].startswith("基準: 直結 12:00"), q["text"])
        # 0dB が見えているときは動かさない
        self.call("set_amplitude", ref_level=20.0)
        n = self.take()
        self.assertNotIn("ref_level", n["detail"]["result"])
        self.assertEqual(self.call("get_state")["amplitude"]["ref_level"], 20.0)

    def test_quick_view_reasons(self):
        st = self.call("get_state")
        self.assertFalse(screen_model.ref_quick_view(st)["visible"])
        self.connect()
        self.call("set_mode", mode="tg")
        q = screen_model.ref_quick_view(self.call("get_state"))
        self.assertTrue(q["visible"])
        self.assertIsNone(q["reason"])
        self.assertEqual(q["text"], "基準なし（dBm で表示）")
        with self.assertRaises(CommandError):
            self.call("reference_take", kind="wide", show_zero="yes")


if __name__ == "__main__":
    unittest.main()
