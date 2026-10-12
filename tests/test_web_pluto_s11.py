"""Web の PlutoSDR のスカラー S11（設計Q §7.8）。状態の ui（ui.pluto・ui.s11・ui.ref_quick）が Qt と同じ関数
（screen_pluto.pluto_view・screen_s11.s11_view・s11_quick）で作られ、命令が通ること。ヘッドレスの Edge で、S11 の
ボタンが使える・範囲はいまの条件だけ・「オープンを測る」で利得を合わせてから測り、選んだ利得の知らせが出る・その後は
受信の利得・「利得を合わせる」・送信の減衰が理由付きで使えない（Qt の test_ui_qt_pluto_s11 と同じ流れ）。
"""
import json
import tempfile
import time
import unittest
from pathlib import Path

from pygigast.api.local import LocalApi, _PlutoSdrBackend, _SimulatorBackend
from pygigast.core.devices.base import load_profile
from pygigast.core.devices.plutosdr import driver as pd
from pygigast.core.devices.plutosdr.fake import FAKE_URI, FakeIioLib
from pygigast.core.instrument import GIGAST_PROFILE_PATH, Instrument
from pygigast.ui_qt import screen_model, screen_pluto, screen_s11, texts
from pygigast.ui_qt.texts_pluto import PLUTO_LABELS
from tests import test_web_server as wst
from tests.test_pluto_s11 import bridge
from tests.test_web_plutosdr import _edge_ok
from tests.test_web_server import UI_WEB, _Server

MHZ = 1_000_000


def _json(v):
    return json.loads(json.dumps(v))


class WebPlutoS11Test(unittest.TestCase):
    post = wst.WebServerTest.post
    get = wst.WebServerTest.get
    tearDown = wst.WebServerTest.tearDown

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        profile = load_profile(GIGAST_PROFILE_PATH)
        sim = _SimulatorBackend(profile)
        sim.speed = 0.05
        self.lib = FakeIioLib(bridge())
        pd._cache = pd._SerialCache()
        pl = _PlutoSdrBackend(lib_factory=lambda: self.lib)
        self.api = LocalApi(Instrument({"simulator": sim, "plutosdr": pl}, profile=profile,
                                       data_root=root / "data", cal_root=root / "cal"))
        self.srv = _Server(self.api)

    def state(self):
        return json.loads(self.get("/api/v1/state")[1])

    def wait_state(self, pred, what):
        end = time.monotonic() + 30
        while True:
            st = self.state()
            if pred(st):
                return st
            self.assertLess(time.monotonic(), end, what)
            time.sleep(0.05)

    def test_views_and_flow(self):
        self.assertEqual(self.post("connect", {"backend": "plutosdr", "port": FAKE_URI})[0], 200)
        st = self.state()
        self.assertIsNone(screen_model.mode_reasons(st)["s11"])
        self.post("set_sweep", {"start_hz": 400 * MHZ, "stop_hz": 402 * MHZ})
        self.post("plutosdr_set", {"tx_atten_db": -10.0, "gain_db": 60.0})
        self.assertEqual(self.post("set_mode", {"mode": "s11"})[0], 200)
        st = self.state()
        self.assertEqual(st["ui"]["s11"], _json(screen_s11.s11_view(st)))
        self.assertEqual(st["ui"]["s11"]["range_fixed"], "fine")
        self.assertEqual(st["ui"]["s11"]["range_reason"], texts.ERRORS["reference_wide_fft"])
        self.assertEqual(st["ui"]["s11"]["open_tip"], texts.LABELS["s11_open_tip_pluto"])
        self.assertEqual(st["ui"]["ref_quick"]["take"]["kind"], "fine")
        self.assertIsNone(st["ui"]["pluto"]["tx_atten_reason"])
        self.assertIsNone(st["ui"]["pluto"]["tg_harmonic"]["reason"])
        self.assertEqual(self.post("reference_take", {"standard": "open", "kind": "fine"})[0], 200)
        st = self.wait_state(lambda s: s["normalization"]["s11_open"] is not None, "オープン")
        v = st["ui"]["pluto"]
        self.assertEqual(v, _json(screen_pluto.pluto_view(st)))
        self.assertEqual(v["gain_reason"], PLUTO_LABELS["s11_locked"])
        self.assertEqual(v["tx_atten_reason"], PLUTO_LABELS["s11_locked"])
        self.assertEqual(v["fit_reason"], PLUTO_LABELS["s11_locked"])
        self.assertNotEqual(v["gain_db"], 60.0)
        code, body = self.post("plutosdr_set", {"tx_atten_db": -20.0})
        self.assertNotEqual(code, 200)
        self.assertEqual(body["error"]["text"], texts.ERRORS["plutosdr_s11_conditions_locked"])
        self.post("reference_take", {"standard": "short"})
        st = self.wait_state(lambda s: (s["normalization"].get("selected") or {}).get("mode") == "s11", "基準")
        self.assertEqual(st["ui"]["pluto"]["tx_atten_reason"], PLUTO_LABELS["s11_locked"])
        self.post("reference_select", {"id": None})
        st = self.state()
        self.assertIsNone(st["ui"]["pluto"]["tx_atten_reason"])
        self.assertIsNone(st["ui"]["pluto"]["gain_reason"])

    def test_texts_and_page_parts(self):
        tx = json.loads(self.get("/api/v1/texts")[1])
        self.assertEqual(tx["PLUTO_LABELS"]["s11_locked"], PLUTO_LABELS["s11_locked"])
        html = (UI_WEB / "index.html").read_text(encoding="utf-8")
        a = html.index('data-part="pluto_tx"')
        part = html[a:html.index("</section>", a)]
        self.assertIn(':disabled="!!plutoView.tx_atten_reason"', part)
        self.assertIn("plutoView.tx_atten_reason ? ' — ' + plutoView.tx_atten_reason", part)
        self.assertIn("s11.open_tip", html)
        self.assertIn("range_fixed", (UI_WEB / "app_s11.js").read_text(encoding="utf-8"))


@unittest.skipUnless(_edge_ok(), "Microsoft Edge が無い")
class WebPlutoS11BrowserTest(unittest.TestCase):
    """ヘッドレスの Edge で通す（Qt の test_open_fits_gain_then_locks_with_reasons と同じ流れ）。"""

    @classmethod
    def setUpClass(cls):
        from tests.test_web_layout import App, EdgePage

        cls.app = App(lan=False, pluto=True)
        # App の偽物をブリッジにする（繋いだまま。受信は次の読みから変わる）
        pl = cls.app.api._inst._backends["plutosdr"]._lib_factory().pluto
        b = bridge()
        pl.bridge, pl.signals, pl.loss_db = True, [], b.loss_db
        pl.directivity_db, pl.dut_gamma = b.directivity_db, b.dut_gamma
        try:
            cls.page = EdgePage()
        except Exception:
            cls.app.close()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.page.close()
        cls.app.close()

    def test_open_fits_gain_then_locks(self):
        from tests.test_web_layout import open_app

        a, p = self.app, self.page
        a.call("set_mode", mode="sa")
        a.call("set_sweep", start_hz=400 * MHZ, stop_hz=402 * MHZ)
        a.call("plutosdr_set", tx_atten_db=-10.0, gain_db=60.0)
        a.call("set_mode", mode="s11")
        open_app(p, a, 1280, 720)
        # モードのボタン（並びは texts.MODES: SA・TG・S11 の順の3つ目）が使える
        modes = p.ev("[...document.querySelectorAll('.modes .mode-btn')].map((b) => [b.textContent.trim(), b.disabled])")
        s11 = next(m for m in modes if m[0] == next(lb for c, lb, _n, _w in texts.MODES if c == "s11"))
        self.assertFalse(s11[1])
        p.ev("gsApp.tab = 'source'; 1")
        p.settle()
        rng = "document.querySelector('[data-field=\"s11_range\"]')"
        self.assertTrue(p.ev(f"{rng}.disabled"))
        self.assertEqual(p.ev(f"{rng}.value"), "fine")
        self.assertIn(texts.ERRORS["reference_wide_fft"], p.ev(f"{rng}.title"))
        btn = "document.querySelector('[data-act=\"s11_%s\"]')"
        self.assertFalse(p.ev((btn % "open") + ".disabled"))
        self.assertIn(texts.LABELS["s11_open_tip_pluto"], p.ev((btn % "open") + ".title"))
        atten = "document.querySelector('[data-field=\"tx_atten\"] input')"
        self.assertFalse(p.ev(f"{atten}.disabled"))
        p.ev((btn % "open") + ".click(); 1")
        p.wait("!!(gsApp.ui.s11 && gsApp.ui.s11.next === 'short')", "次はショート", timeout=40)
        g = a.call("get_state")["plutosdr"]["gain_db"]
        want = texts.tpl("ref_open_measured_fitted", gain=f"{g:g}", before="60 dB")
        p.wait(f"gsApp.status.endsWith({json.dumps(want, ensure_ascii=False)})", "選んだ利得の知らせ")
        p.settle()
        why = PLUTO_LABELS["s11_locked"]
        self.assertTrue(p.ev(f"{atten}.disabled"))
        self.assertIn(why, p.ev("document.querySelector('[data-field=\"tx_atten\"]').title || "
                                "document.querySelector('[data-field=\"tx_atten\"] input').title"))
        p.ev("gsApp.tab = 'trace'; 1")
        p.settle()
        gain = "document.querySelector('[data-field=\"pluto_gain\"]')"
        self.assertTrue(p.ev(f"{gain}.querySelector('input').disabled"))
        self.assertIn(why, p.ev(f"{gain}.title || {gain}.querySelector('input').title"))
        self.assertEqual(p.ev(f"{gain}.querySelector('input').value"), f"{g:g} dB")
        fit = "document.querySelector('[data-act=\"pluto_fit\"]')"
        self.assertTrue(p.ev(f"{fit}.disabled"))
        self.assertIn(why, p.ev(f"{fit}.title"))
        self.assertEqual(p.errors, [])
        a.call("set_mode", mode="sa")


if __name__ == "__main__":
    unittest.main()
