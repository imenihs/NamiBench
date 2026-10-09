"""Web の口の PlutoSDR（設計Q §7）: 状態の ui.pluto が Qt と同じ関数（screen_pluto.pluto_view）で作られ、命令が通ること。

libiio の偽物と本物の uvicorn で通す。
"""
import json
import re
import tempfile
import unittest
from pathlib import Path

from pygigast.api.local import LocalApi, _PlutoSdrBackend, _SimulatorBackend
from pygigast.core.devices.base import load_profile
from pygigast.core.devices.plutosdr import driver as pd
from pygigast.core.devices.plutosdr.fake import FAKE_URI, FakeIioLib
from pygigast.core.instrument import GIGAST_PROFILE_PATH, Instrument
from pygigast.ui_qt import screen_pluto, texts
from tests import test_web_server as wst
from tests.test_web_server import UI_WEB, _Server

MHZ = 1_000_000


class WebPlutoTest(unittest.TestCase):
    post = wst.WebServerTest.post
    get = wst.WebServerTest.get
    ws = wst.WebServerTest.ws
    recv_until = staticmethod(wst.WebServerTest.recv_until)
    tearDown = wst.WebServerTest.tearDown

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

    def state(self):
        return json.loads(self.get("/api/v1/state")[1])

    def test_ui_follows_device_and_commands(self):
        st = self.state()
        self.assertFalse(st["ui"]["pluto"]["shown"])
        self.assertEqual(st["ui"]["pluto"]["reason"], texts.PLUTO_LABELS["not_pluto"])
        self.assertEqual(self.post("connect", {"backend": "plutosdr", "port": FAKE_URI})[0], 200)
        st = self.state()
        ui = st["ui"]["pluto"]
        self.assertTrue(ui["shown"])
        self.assertEqual(ui, json.loads(json.dumps(screen_pluto.pluto_view(st))))
        self.assertEqual(len(ui["gains"]), 76)
        self.assertEqual(self.post("plutosdr_set", {"gain_db": 20.0})[0], 200)
        self.assertEqual(self.state()["plutosdr"]["gain_db"], 20.0)
        code, body = self.post("plutosdr_set", {"tx_atten_db": 3.0})
        self.assertNotEqual(code, 200)
        # DLL のフォルダはブラウザから変えない（設計A §4.3.2）
        code, body = self.post("plutosdr_set", {"dll_folder": "C:/x"})
        self.assertNotEqual(code, 200)
        # TG の「広く取る」は FFT の機器では理由付きで使えない
        self.post("set_mode", {"mode": "tg"})
        self.assertEqual(self.state()["ui"]["tg_wide_reason"], texts.ERRORS["reference_wide_fft"])
        self.assertEqual(self.state()["ui"]["ref_quick"]["take"]["kind"], "fine")

    def test_page_has_same_parts_as_qt(self):
        html = (UI_WEB / "index.html").read_text(encoding="utf-8")
        a = html.index('data-tab="trace"')
        b = html.index('data-tab="marker"')
        secs = re.findall(r'<section class="tsec" data-sec="(\w+)"', html[a:b])
        self.assertEqual(secs[-2:], ["rtlsdr", "plutosdr"])  # 機器に固有の欄は最後（Qt の add_device_section と同じ順）
        self.assertIn('data-part="pluto_tx"', html)
        win = (UI_WEB / "app_windows.js").read_text(encoding="utf-8")
        self.assertIn("page === 'plutosdr'", win)


if __name__ == "__main__":
    unittest.main()
