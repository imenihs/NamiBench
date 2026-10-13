"""Web の PlutoSDR の TG 高調波の平均除去（設計Q §7.7）: 状態の ui.pluto.tg_harmonic が Qt と同じ関数
（screen_pluto.tg_harmonic_view）で作られ、命令が通ること。ヘッドレスの Edge で、TG のときだけ選べ、選択肢の文字・
選んだ回数・使えない理由が Qt と同じになることを確かめる。
"""
import json
import re
import tempfile
import time
import unittest
from pathlib import Path

from namibench.api.local import LocalApi, _PlutoSdrBackend, _SimulatorBackend
from namibench.core.devices.base import load_profile
from namibench.core.devices.plutosdr import driver as pd
from namibench.core.devices.plutosdr.fake import FAKE_URI, FakeIioLib
from namibench.core.instrument import GIGAST_PROFILE_PATH, Instrument
from namibench.ui_qt import screen_pluto
from namibench.ui_qt.texts_pluto import PLUTO_LABELS, TG_HARMONIC_CHOICES
from tests import test_web_server as wst
from tests.test_web_plutosdr import _edge_ok
from tests.test_web_server import UI_WEB, _Server

MHZ = 1_000_000


class WebTgHarmonicTest(unittest.TestCase):
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

    def test_ui_view_and_command(self):
        st = self.state()
        v = st["ui"]["pluto"]["tg_harmonic"]
        self.assertEqual(v["reason"], PLUTO_LABELS["not_pluto"])
        self.assertEqual(self.post("connect", {"backend": "plutosdr", "port": FAKE_URI})[0], 200)
        self.assertEqual(self.state()["ui"]["pluto"]["tg_harmonic"]["reason"], PLUTO_LABELS["tg_harmonic_not_tg"])
        self.post("set_sweep", {"start_hz": 985 * MHZ, "stop_hz": 1015 * MHZ})
        self.post("set_mode", {"mode": "tg"})
        st = self.state()
        v = st["ui"]["pluto"]["tg_harmonic"]
        self.assertEqual(v, json.loads(json.dumps(screen_pluto.tg_harmonic_view(st))))
        self.assertIsNone(v["reason"])
        self.assertEqual([it["code"] for it in v["items"]], [1, 4, 8])
        self.assertTrue(all("秒" in it["text"] for it in v["items"]))
        self.assertEqual(self.post("plutosdr_set", {"tg_harmonic_avg": 4})[0], 200)
        st = self.state()
        self.assertEqual(st["ui"]["pluto"]["tg_harmonic"]["value"], 4)
        self.assertAlmostEqual(st["sweep"]["estimated_sweep_s"], st["plutosdr"]["tg_harmonic_estimates"]["4"])
        self.assertNotEqual(self.post("plutosdr_set", {"tg_harmonic_avg": 3})[0], 200)

    def test_texts_and_page_parts(self):
        tx = json.loads(self.get("/api/v1/texts")[1])
        self.assertEqual(tx["TG_HARMONIC_CHOICES"], TG_HARMONIC_CHOICES)
        self.assertEqual(tx["PLUTO_LABELS"]["tg_harmonic"], "TG 高調波の平均除去")
        html = (UI_WEB / "index.html").read_text(encoding="utf-8")
        a = html.index('data-part="pluto_tx"')
        b = html.index("</section>", a)
        part = html[a:b]
        # 送信の減衰の下（Qt の PlutoTxPanel と同じ並び）
        self.assertLess(part.index('data-field="tx_atten"'), part.index('data-field="tg_harmonic"'))
        self.assertRegex(part, r'<select class="w-tgharm" data-field="tg_harmonic"')
        css = (UI_WEB / "style.css").read_text(encoding="utf-8")
        self.assertTrue(re.search(r"\.w-tgharm \{ width: var\(--col-tgharm", css))


@unittest.skipUnless(_edge_ok(), "Microsoft Edge が無い")
class WebTgHarmonicBrowserTest(unittest.TestCase):
    """ヘッドレスの Edge で選ぶ（Qt の test_tg_shows_estimates_and_sends_the_choice と同じ流れ）。"""

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

    def pl(self):
        return self.app.call("get_state")["plutosdr"]

    def test_disabled_outside_tg_then_choose(self):
        from tests.test_web_layout import open_app

        a, p = self.app, self.page
        a.call("set_mode", mode="sg", sg_hz=2_401_000_000)
        open_app(p, a, 1280, 720)
        p.ev("gsApp.tab = 'source'; 1")
        p.settle()
        sel = "document.querySelector('[data-field=tg_harmonic]')"
        self.assertTrue(p.ev(f"{sel}.disabled"))
        self.assertIn(PLUTO_LABELS["tg_harmonic_not_tg"], p.ev(f"{sel}.title"))
        texts_of = f"[...{sel}.options].map((o) => o.textContent.trim())"
        self.assertEqual(p.ev(texts_of), list(TG_HARMONIC_CHOICES.values()))
        width = p.ev(f"{sel}.getBoundingClientRect().width")
        col = p.ev("parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--col-tgharm'))")
        self.assertAlmostEqual(width, col, delta=0.5)
        a.call("set_sweep", start_hz=985 * MHZ, stop_hz=1015 * MHZ)
        a.call("set_mode", mode="tg")
        p.wait(f"!{sel}.disabled", "TG で使える")
        st = a.call("get_state")
        want = [it["text"] for it in screen_pluto.tg_harmonic_view(st)["items"]]
        p.wait(f"JSON.stringify({texts_of}) === {json.dumps(json.dumps(want, ensure_ascii=False, separators=(",", ":")))}", "名前と見積もり")
        self.assertEqual(p.ev(f"{sel}.title"), PLUTO_LABELS["tg_harmonic_tip"])
        p.ev(f"(() => {{ const s = {sel}; s.value = '8'; s.dispatchEvent(new Event('change')); return 1; }})()")
        end = time.monotonic() + 15
        while self.pl()["tg_harmonic_avg"] != 8:
            self.assertLess(time.monotonic(), end, "8回を選んだ")
            time.sleep(0.05)
        self.assertAlmostEqual(p.ev(f"{sel}.getBoundingClientRect().width"), width, delta=0.5)  # 秒の桁で動かない
        a.call("plutosdr_set", tg_harmonic_avg=1)
        p.wait(f"{sel}.value === '1'", "コアから変わったら欄も映す")
        self.assertEqual(p.errors, [])


if __name__ == "__main__":
    unittest.main()
