"""Web の PlutoSDR の TG の基準（設計Q §7.9）。状態の ui（ui.pluto・ui.tg_take_reason・ui.tg_fine_tip・ui.ref_quick）が
Qt と同じ関数（screen_pluto.pluto_view・screen_s11・screen_model.ref_quick_view）で作られ、知らせの文も同じ関数
（texts.notice_view）で作られること。ヘッドレスの Edge で、「いまの条件で細かく取る」で利得を合わせてから基準を取り、
選んだ利得の知らせが出る・その後は受信の利得・「利得を合わせる」・送信の減衰が理由付きで使えない（Qt の
test_ui_qt_pluto_tg_ref と同じ流れ）。
"""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from namibench.api.local import LocalApi, _PlutoSdrBackend, _SimulatorBackend
from namibench.api.web import view as web_view
from namibench.core.devices.base import load_profile
from namibench.core.devices.plutosdr import driver as pd
from namibench.core.devices.plutosdr.fake import FAKE_URI, FakeIioLib, FakePluto
from namibench.core.instrument import GIGAST_PROFILE_PATH, Instrument
from namibench.ui_qt import screen_model, screen_pluto, screen_s11, texts
from namibench.ui_qt.texts_devices import REF_QUICK_LABELS
from namibench.ui_qt.texts_pluto import PLUTO_LABELS
from tests import test_web_server as wst
from tests.test_web_plutosdr import _edge_ok
from tests.test_web_server import UI_WEB, _Server

MHZ = 1_000_000


def _json(v):
    return json.loads(json.dumps(v))


class WebPlutoTgRefTest(unittest.TestCase):
    post = wst.WebServerTest.post
    get = wst.WebServerTest.get
    tearDown = wst.WebServerTest.tearDown

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        profile = load_profile(GIGAST_PROFILE_PATH)
        sim = _SimulatorBackend(profile)
        sim.speed = 0.05
        self.lib = FakeIioLib(FakePluto(signals=[], loss_db=10.0))  # TX → 損失 10dB → RX の直結
        pd._cache = pd._SerialCache()
        pl = _PlutoSdrBackend(lib_factory=lambda: self.lib)
        self.inst = Instrument({"simulator": sim, "plutosdr": pl}, profile=profile,
                               data_root=root / "data", cal_root=root / "cal")
        self.api = LocalApi(self.inst)
        self.srv = _Server(self.api)
        self.notices = []
        self.lock = threading.Lock()

        def on_event(kind, payload):
            if kind == "notice":
                with self.lock:
                    self.notices.append(payload)

        self.inst.subscribe(on_event)

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
        self.post("set_sweep", {"start_hz": 400 * MHZ, "stop_hz": 402 * MHZ})
        self.post("plutosdr_set", {"tx_atten_db": -10.0, "gain_db": 60.0})
        self.assertEqual(self.post("set_mode", {"mode": "tg"})[0], 200)
        st = self.state()
        self.assertIsNone(st["ui"]["tg_take_reason"])
        self.assertEqual(st["ui"]["tg_fine_tip"], screen_s11.tg_fine_tip(st))
        self.assertEqual(st["ui"]["tg_fine_tip"], texts.LABELS["take_fine_tip_pluto"])
        self.assertEqual(st["ui"]["ref_quick"], _json(screen_model.ref_quick_view(st)))
        self.assertTrue(st["ui"]["ref_quick"]["button_tip"].startswith(REF_QUICK_LABELS["tip_pluto"]))
        self.assertIsNone(st["ui"]["pluto"]["tx_atten_reason"])
        self.assertEqual(self.post("reference_take", {"kind": "fine"})[0], 200)
        st = self.wait_state(lambda s: (s["normalization"].get("selected") or {}).get("mode") == "tg", "基準")
        v = st["ui"]["pluto"]
        self.assertEqual(v, _json(screen_pluto.pluto_view(st)))
        for k in ("gain_reason", "tx_atten_reason", "fit_reason"):
            self.assertEqual(v[k], PLUTO_LABELS["tg_locked"], k)
        g = st["plutosdr"]["gain_db"]
        self.assertNotEqual(g, 60.0)
        # 知らせの文（Web が出す view.text）は選んだ利得を言う
        with self.lock:
            got = [web_view.notice_view(n, st)["view"]["text"] for n in self.notices]
        self.assertIn(texts.tpl("tg_ref_gain_fitted", gain=f"{g:g}", before="60 dB"), got)
        name = st["normalization"]["selected"]["name"]
        self.assertIn(texts.tpl("ref_taken_fitted", gain=f"{g:g}", before="60 dB", name=name), got)
        code, body = self.post("plutosdr_set", {"tx_atten_db": -20.0})
        self.assertNotEqual(code, 200)
        self.assertEqual(body["error"]["text"], texts.ERRORS["plutosdr_tg_conditions_locked"])
        self.assertEqual(self.post("plutosdr_set", {"tx_atten_db": -10.0})[0], 200)  # 同じ値は断らない
        self.post("reference_select", {"id": None})
        st = self.state()
        self.assertIsNone(st["ui"]["pluto"]["tx_atten_reason"])
        self.assertIsNone(st["ui"]["pluto"]["gain_reason"])

    def test_texts_and_page_parts(self):
        tx = json.loads(self.get("/api/v1/texts")[1])
        self.assertEqual(tx["PLUTO_LABELS"]["tg_locked"], PLUTO_LABELS["tg_locked"])
        self.assertEqual(tx["ERRORS"]["tg_ref_fit_failed"], texts.ERRORS["tg_ref_fit_failed"])
        html = (UI_WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn("(ui.tg_fine_tip || W('take_fine_tip'))", html)


@unittest.skipUnless(_edge_ok(), "Microsoft Edge が無い")
class WebPlutoTgRefBrowserTest(unittest.TestCase):
    """ヘッドレスの Edge で通す（Qt の test_take_fits_gain_then_locks_with_reasons と同じ流れ）。"""

    @classmethod
    def setUpClass(cls):
        from tests.test_web_layout import App, EdgePage

        cls.app = App(lan=False, pluto=True)
        # App の偽物を TX → 損失 10dB → RX の直結にする（繋いだまま。受信は次の読みから変わる）
        pl = cls.app.api._inst._backends["plutosdr"]._lib_factory().pluto
        pl.signals, pl.loss_db = [], 10.0
        try:
            cls.page = EdgePage()
        except Exception:
            cls.app.close()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.page.close()
        cls.app.close()

    def test_take_fits_gain_then_locks(self):
        from tests.test_web_layout import open_app

        a, p = self.app, self.page
        a.call("set_mode", mode="sa")
        a.call("reference_select", id=None)
        a.call("set_sweep", start_hz=400 * MHZ, stop_hz=402 * MHZ)
        a.call("plutosdr_set", tx_atten_db=-10.0, gain_db=60.0)
        a.call("set_mode", mode="tg")
        open_app(p, a, 1280, 720)
        p.ev("window._notes = []; const _o = gsApp.note; gsApp.note = function (k, t) { window._notes.push(t); "
             "return _o.call(this, k, t); }; 1")
        p.ev("gsApp.tab = 'source'; 1")
        p.settle()
        fine = "document.querySelector('[data-act=\"take_fine\"]')"
        self.assertFalse(p.ev(f"{fine}.disabled"))
        self.assertIn(texts.LABELS["take_fine_tip_pluto"], p.ev(f"{fine}.title"))
        atten = "document.querySelector('[data-field=\"tx_atten\"] input')"
        self.assertFalse(p.ev(f"{atten}.disabled"))
        p.ev(f"{fine}.click(); 1")
        end = time.monotonic() + 40
        while not (a.call("get_state")["normalization"].get("selected") or {}).get("mode") == "tg":
            self.assertLess(time.monotonic(), end, "基準")
            time.sleep(0.1)
        st = a.call("get_state")
        g = st["plutosdr"]["gain_db"]
        name = st["normalization"]["selected"]["name"]
        want = texts.tpl("ref_taken_fitted", gain=f"{g:g}", before="60 dB", name=name)
        p.wait(f"window._notes.some((t) => t && t.includes({json.dumps(want, ensure_ascii=False)}))",
               "選んだ利得の知らせ")
        p.settle()
        why = PLUTO_LABELS["tg_locked"]
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
        a.call("reference_select", id=None)
        a.call("set_mode", mode="sa")


if __name__ == "__main__":
    unittest.main()
