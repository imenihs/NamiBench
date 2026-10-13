"""Web の PlutoSDR の基準の補正（設計Q §7.10）: 状態の ui.pluto.xo が Qt と同じ関数（screen_pluto.xo_view）で作られ、
命令が通り、断りの文が Qt と同じこと。設定の画面の欄は一覧を持たない db-field（Qt の DecimalField）で、ヘッドレスの Edge で
打つ・範囲の外を打つ・断りの知らせを確かめる。"""
import json
import re
import tempfile
import time
import unittest
from pathlib import Path

from pygigast.api.local import LocalApi, _PlutoSdrBackend, _SimulatorBackend
from pygigast.core.devices.base import load_profile
from pygigast.core.devices.plutosdr import driver as pd
from pygigast.core.devices.plutosdr.fake import FAKE_URI, FakeIioLib
from pygigast.core.instrument import GIGAST_PROFILE_PATH, Instrument
from pygigast.ui_qt import screen_pluto, texts
from pygigast.ui_qt.fields import range_note
from tests import test_web_server as wst
from tests.test_web_plutosdr import _edge_ok
from tests.test_web_server import UI_WEB, _Server


class WebPlutoXoTest(unittest.TestCase):
    post = wst.WebServerTest.post
    get = wst.WebServerTest.get
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

    def test_ui_and_commands(self):
        st = self.state()
        self.assertEqual(st["ui"]["pluto"]["xo"]["reason"], texts.PLUTO_LABELS["not_pluto"])
        self.assertEqual(self.post("connect", {"backend": "plutosdr", "port": FAKE_URI})[0], 200)
        st = self.state()
        ui = st["ui"]["pluto"]["xo"]
        self.assertEqual(ui, json.loads(json.dumps(screen_pluto.xo_view(st))))
        self.assertEqual((ui["ppm"], ui["lo"], ui["hi"], ui["decimals"], ui["unit"], ui["reason"]),
                         (0.0, -200.0, 200.0, 3, "ppm", None))
        self.assertEqual(ui["hz_text"], "機器の基準 40,000,000 Hz")
        code, body = self.post("plutosdr_xo_set", {"ppm": 2.5})
        self.assertEqual(code, 200)
        self.assertEqual(self.lib.pluto.xo_hz, 40_000_100)
        self.assertEqual(self.state()["ui"]["pluto"]["xo"]["hz_text"], "機器の基準 40,000,100 Hz")
        # 測っている間は断る。文は Qt と同じ（texts.error_text）
        self.post("run", {"mode": "continuous"})
        code, body = self.post("plutosdr_xo_set", {"ppm": 1.0})
        self.assertNotEqual(code, 200)
        self.assertEqual(body["error"]["code"], "plutosdr_xo_busy")
        self.assertEqual(body["error"]["text"], texts.ERRORS["plutosdr_xo_busy"])
        self.assertEqual(self.state()["ui"]["pluto"]["xo"]["reason"], texts.ERRORS["plutosdr_xo_busy"])
        self.post("stop")

    def test_page_has_same_parts_as_qt(self):
        win = (UI_WEB / "app_windows.js").read_text(encoding="utf-8")
        a = win.index("page === 'plutosdr'")
        part = win[a:win.index('<div class="modal-btns">', a)]
        # 一覧を渡さない db-field（Qt の DecimalField と同じ: 小数 3 桁・範囲は ui.pluto.xo）。断りは欄の下（notes.pluto_xo）
        self.assertRegex(part, r'<db-field data-field="pluto_xo"[^>]*:unit="a.plutoXo.unit"[^>]*:lo="a.plutoXo.lo"'
                               r'[^>]*:hi="a.plutoXo.hi"[^>]*:decimals="a.plutoXo.decimals"[^>]*:field-note="a.notes.pluto_xo"')
        self.assertNotRegex(part, r'<db-field data-field="pluto_xo"[^>]*:choices')
        self.assertIn("a.plutoXo.hz_text", part)
        self.assertIn("a.plutoXo.warn_text", part)
        self.assertIn("a.setPlutoXo(0, true)", part)
        fields = (UI_WEB / "fields.js").read_text(encoding="utf-8")
        self.assertIn(':choices="items.length ? items : null"', fields)  # 一覧が空なら ▼ を付けない
        rtl = (UI_WEB / "app_rtl.js").read_text(encoding="utf-8")
        self.assertTrue(re.search(r'this\.cmd\("plutosdr_xo_set", \{ ppm: Number\(v\) \}, \{ field: "pluto_xo" \}\)', rtl))


@unittest.skipUnless(_edge_ok(), "Microsoft Edge が無い")
class WebPlutoXoBrowserTest(unittest.TestCase):
    """ヘッドレスの Edge で、設定の PlutoSDR のページの基準の補正の欄に打つ・範囲の外を打つ・測っている間の断り
    （Qt の test_ui_qt_pluto_xo と同じ流れ）。"""

    SEL = "document.querySelector('[data-field=pluto_xo]')"

    @classmethod
    def setUpClass(cls):
        from tests.test_web_layout import App, EdgePage

        cls.app = App(lan=False, pluto=True)
        try:
            cls.page = EdgePage()
        except Exception:
            cls.app.close()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.page.close()
        cls.app.close()

    def xo(self):
        return self.app.call("get_state")["plutosdr"]["xo"]

    def wait_state(self, cond, what):
        end = time.monotonic() + 15
        while not cond(self.xo()):
            self.assertLess(time.monotonic(), end, what)
            time.sleep(0.05)

    def type(self, v):
        self.page.ev(("(() => {{ const i = {sel}.querySelector('input'); i.focus(); i.value = '{v}'; "
                      "i.dispatchEvent(new Event('input')); i.dispatchEvent(new KeyboardEvent('keydown', {{key: 'Enter'}})); "
                      "return 1; }})()").format(sel=self.SEL, v=v))

    def test_type_range_and_refusal(self):
        from tests.test_web_layout import open_app

        a, p = self.app, self.page
        open_app(p, a, 1280, 720)
        p.ev("gsApp.openSettings('plutosdr'); 1")
        p.settle()
        self.assertEqual(p.ev(f"{self.SEL}.querySelector('input').value"), "0.000 ppm")
        self.assertFalse(p.ev(f"!!{self.SEL}.querySelector('select')"))  # 一覧は無い（Qt の DecimalField と同じ）
        self.type("-1.5")
        self.wait_state(lambda x: x["ppm"] == -1.5 and x["hz"] == 39_999_940, "打った補正を書いた")
        p.wait(f"{self.SEL}.querySelector('input').value === '−1.500 ppm'", "欄が映す")
        p.wait("document.querySelector('.setwin-body').textContent.includes('機器の基準 39,999,940 Hz')", "読み戻した値")
        # 範囲の外は送らず、欄の下に範囲（Qt と同じ文）
        self.type("300")
        p.wait(f"!!{self.SEL}.querySelector('.note-pop')", "範囲の知らせ")
        self.assertEqual(p.ev(f"{self.SEL}.querySelector('.note-pop span').textContent"), range_note(-200.0, 200.0, "ppm"))
        self.assertEqual(self.xo()["ppm"], -1.5)
        # 測っている間: 欄は理由付きで使えない。届いた断りは欄の下（notes.pluto_xo）
        a.call("run", mode="continuous")
        try:
            p.wait(f"{self.SEL}.querySelector('input').disabled", "測っている間は使えない")
            self.assertIn(texts.ERRORS["plutosdr_xo_busy"], p.ev(f"{self.SEL}.title"))
            p.ev("gsApp.setPlutoXo(2); 1")
            p.wait(f"!!{self.SEL}.querySelector('.note-pop') && {self.SEL}.querySelector('.note-pop span').textContent"
                   f" === {json.dumps(texts.ERRORS['plutosdr_xo_busy'])}", "断りの知らせ")
        finally:
            a.call("stop")
        self.assertEqual(p.errors, [])


if __name__ == "__main__":
    unittest.main()
