"""Web の口の機器の一覧（設計J §4.7）と上段の「基準を取る」（J §4.2）。文はサーバーが Qt と同じ関数で足す。

偽の GigaSt の種類とシミュレータ、本物の uvicorn で通す。
"""
import json
import tempfile
import unittest
from pathlib import Path

from pygigast.api.local import LocalApi, _SimulatorBackend
from pygigast.core.devices.base import load_profile
from pygigast.core.instrument import GIGAST_PROFILE_PATH, Instrument
from pygigast.ui_qt import texts
from tests.device_helpers import FakeGigaBackend
from tests.test_web_server import WebServerTest, _Server

SERIAL = "FT00AB12"


class WebDevicesTest(unittest.TestCase):
    post = WebServerTest.post
    get = WebServerTest.get
    ws = WebServerTest.ws
    recv_until = staticmethod(WebServerTest.recv_until)
    tearDown = WebServerTest.tearDown

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        profile = load_profile(GIGAST_PROFILE_PATH)
        sim = _SimulatorBackend(profile)
        sim.speed = 0.05
        self.giga = FakeGigaBackend(profile)
        self.api = LocalApi(Instrument({"gigast": self.giga, "simulator": sim}, profile=profile,
                                       data_root=root / "data", cal_root=root / "cal"))
        self.srv = _Server(self.api)

    def test_list_view_and_plug_event(self):
        st, body = self.post("list_devices", {"watch_s": 5})
        self.assertEqual(st, 200)
        self.assertTrue(body["empty"])
        self.assertEqual(body["rows"], [])
        self.assertEqual(body["simulator"]["kind"], "simulator")
        with self.ws() as ws:
            self.giga.plug("COM7", SERIAL)
            p = self.recv_until(ws, lambda k, p: k == "devices" and p["rows"])
        self.assertFalse(p["empty"])
        row = p["rows"][0]
        self.assertEqual(row["view"]["name"], "GigaSt v5 AB12")
        self.assertEqual(row["view"]["state_text"], texts.DEVICE_STATES["first_time_needs_probe"])
        self.assertIn("COM7", row["view"]["details"])

    def test_connect_failure_has_next_step(self):
        self.giga.plug("COM7", SERIAL)
        self.giga.probe_result = False
        st, body = self.post("connect", {"backend": "gigast", "port": "COM7"})
        self.assertEqual((st, body["error"]["code"]), (400, "probe_failed"))
        self.assertEqual(body["error"]["text_next"],
                         texts.connect_failed_text("", texts.ERRORS["probe_failed"], "probe_failed"))
        self.giga.probe_result = True
        self.giga.busy.add("COM7")
        st, body = self.post("connect", {"backend": "gigast", "port": "COM7"})
        self.assertEqual(body["error"]["code"], "port_in_use")
        self.assertIn("GS5.exe", body["error"]["text"])

    def test_state_ui_status_and_ref_quick(self):
        ui = json.loads(self.get("/api/v1/state")[1])["ui"]
        # 未接続の文は Qt と同じ表の文（メニューの項目の名前が変わっても追いかける）
        self.assertEqual(ui["status"][0]["text"], texts.TEMPLATES["status_conn_none"])
        self.assertIn(texts.MENU_ITEMS["connect"], ui["status"][0]["text"])
        self.assertFalse(ui["ref_quick"]["visible"])
        self.post("connect", {"backend": "simulator", "serial": "SIM00001"})
        self.post("set_mode", {"mode": "tg"})
        ui = json.loads(self.get("/api/v1/state")[1])["ui"]
        self.assertTrue(ui["ref_quick"]["visible"])
        self.assertIsNone(ui["ref_quick"]["reason"])
        self.assertEqual(ui["ref_quick"]["text"], "基準なし（dBm で表示）")

    def test_top_bar_quick_has_no_progress_parts(self):
        """上段の「基準を取る」の隣に、ちらつく棒・二つ目の「やめる」・進み具合の文を置かない（進み具合は掃引の
        進み具合の棒と処理の帯）。画面の部品に棒と「やめる」が無く、表示の中身にも持たない。"""
        import re
        from pathlib import Path
        html = (Path(__file__).resolve().parents[1] / "pygigast" / "ui_web" / "index.html").read_text(encoding="utf-8")
        block = re.search(r'<div class="refquick".*?\n\s*</div>', html, re.S).group(0)
        self.assertNotIn("<progress", block)
        self.assertNotIn("cancel", block)
        self.assertEqual(block.count("<button"), 1)
        self.post("connect", {"backend": "simulator", "serial": "SIM00001"})
        self.post("set_mode", {"mode": "tg"})
        q = json.loads(self.get("/api/v1/state")[1])["ui"]["ref_quick"]
        for key in ("taking", "task_id", "cancelling", "progress_text"):
            self.assertNotIn(key, q)


if __name__ == "__main__":
    unittest.main()
