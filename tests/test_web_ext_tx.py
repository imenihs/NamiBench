"""Web の口の外部 IQ 送信の口（設計S §1 §7）: 状態の ui.ext_tx と状態バーの項目が Qt と同じ関数（screen_exttx）で作られ、
命令（受け付ける・ポート・減衰・止める）がブラウザから通ること。ページに Qt と同じ部品があること。

libiio の偽物と本物の uvicorn、127.0.0.1 の口に繋ぐ試験のクライアントで通す。
"""
import json
import tempfile
import unittest
from pathlib import Path

from namibench.api.local import LocalApi, _PlutoSdrBackend, _SimulatorBackend
from namibench.core.devices.base import load_profile
from namibench.core.devices.plutosdr import driver as pd
from namibench.core.devices.plutosdr.fake import FAKE_URI, FakeIioLib
from namibench.core.instrument import GIGAST_PROFILE_PATH, Instrument
from namibench.ui_qt import screen_exttx, screen_pluto, texts
from tests import test_web_server as wst
from tests.exttx_helpers import RawClient, free_port
from tests.test_web_server import UI_WEB, _Server

MHZ = 1_000_000


class WebExtTxTest(unittest.TestCase):
    post = wst.WebServerTest.post
    get = wst.WebServerTest.get

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        profile = load_profile(GIGAST_PROFILE_PATH)
        sim = _SimulatorBackend(profile)
        sim.speed = 0.05
        self.lib = FakeIioLib()
        pd._cache = pd._SerialCache()
        pl = _PlutoSdrBackend(lib_factory=lambda: self.lib)
        self.api = LocalApi(Instrument({"simulator": sim, "plutosdr": pl}, profile=profile,
                                       data_root=root / "data", cal_root=root / "cal"))
        self.srv = _Server(self.api)
        self.client = None

    def tearDown(self):
        if self.client is not None:
            self.client.close()
        wst.WebServerTest.tearDown(self)

    def state(self):
        return json.loads(self.get("/api/v1/state")[1])

    def test_ui_and_commands(self):
        port = free_port()
        self.assertEqual(self.post("connect", {"backend": "plutosdr", "port": FAKE_URI})[0], 200)
        self.post("set_sweep", {"start_hz": 1574 * MHZ, "stop_hz": 1577 * MHZ})
        self.post("run", {"mode": "continuous"})
        self.assertEqual(self.post("ext_tx_set", {"enabled": True, "port": port})[0], 200)
        st = self.state()
        ui = st["ui"]["ext_tx"]
        self.assertEqual(ui["settings"], json.loads(json.dumps(screen_exttx.settings_view(st))))
        self.assertEqual(ui["status"]["text"], f"外部 IQ 待ち受け :{port}")
        self.client = RawClient(port)
        self.client.wait_type("welcome")
        self.assertEqual(self.client.start()["type"], "ready")
        self.client.feed()
        self.client.wait_type("on")
        st = self.state()
        ui = st["ui"]
        self.assertTrue(ui["ext_tx"]["tx"]["shown"])
        self.assertEqual(ui["ext_tx"]["tx"]["rate_text"], "2.6MS/s")
        self.assertTrue(ui["pluto"]["tx_atten_ext"])
        self.assertEqual(ui["pluto"]["tx_state"], "出力中: 外部 IQ（test）")
        item = next(i for i in ui["status"] if i["key"] == "exttx")
        self.assertTrue(item["stop"])
        self.assertEqual(ui["mode_reasons"]["tg"], "外部のアプリケーションが送信しています")
        self.assertEqual(self.post("ext_tx_atten", {"atten_db": -20.0})[0], 200)
        self.assertEqual(self.state()["ext_tx"]["atten_db"], -20.0)
        self.assertNotEqual(self.post("set_mode", {"mode": "tg"})[0], 200)
        self.assertEqual(self.post("ext_tx_stop", {})[0], 200)
        self.assertEqual(self.client.wait_type("stopped")["reason"], "user")

    def test_undo_during_stream_notes_rbw(self):
        """元に戻すで積んだ RBW が外部 IQ の速さで使えないとき、Qt と同じ知らせ（RBW の欄）を返す（設計S §1 §3.2）。"""
        port = free_port()
        self.assertEqual(self.post("connect", {"backend": "plutosdr", "port": FAKE_URI})[0], 200)
        self.post("set_sweep", {"start_hz": 1574 * MHZ, "stop_hz": 1577 * MHZ, "rbw_hz": 24000})
        self.post("set_sweep", {"rbw_hz": 1500})
        self.post("run", {"mode": "continuous"})
        self.assertEqual(self.post("ext_tx_set", {"enabled": True, "port": port})[0], 200)
        self.client = RawClient(port)
        self.client.wait_type("welcome")
        self.assertEqual(self.client.start()["type"], "ready")
        self.client.feed()
        self.client.wait_type("on")
        code, r = self.post("undo", {})
        self.assertEqual(code, 200)
        notes = {a["field"]: a["text"] for a in r["texts"]["adjustments"]}
        self.assertIn(texts.REASONS["rbw_sample_rate"], notes["rbw_hz"])
        sw = self.state()["sweep"]
        self.assertEqual((sw["rbw_hz"], sw["rbw_preference_hz"]), (12000, 24000))

    def test_page_has_same_parts_as_qt(self):
        html = (UI_WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-part="ext_tx"', html)
        self.assertIn("s.key === 'exttx' && s.stop", html)
        js = (UI_WEB / "app_windows.js").read_text(encoding="utf-8")
        self.assertIn("page === 'ext_tx'", js)
        self.assertTrue(callable(screen_pluto.pluto_view))


if __name__ == "__main__":
    unittest.main()
