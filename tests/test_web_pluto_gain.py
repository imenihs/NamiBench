"""Web の PlutoSDR の受信の利得の補正（設計Q §7.6）。Qt の test_ui_qt_pluto_gain と同じ中身・同じ動き（J §5）。

- 状態の ui.cal（Qt と同じ cal_view.view）・ui.pluto（Qt と同じ screen_pluto.pluto_view）に、作業の行・印・使えない理由
- メニューの項目・語の表・画面の部品（測るの一覧・減衰器の欄・始める・印の行）が Qt と同じ
- ブラウザで: メニューから開く → 次へ → 始める → 進み具合 → 周波数ごとの行 → 確かめる → 保存 → 印が消える。Edge が無い
  ときは飛ばす
"""
from __future__ import annotations

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
from pygigast.ui_qt import cal_view, screen_pluto
from pygigast.ui_qt import texts as qt_texts
from pygigast.ui_qt.texts_pluto import PLUTO_LABELS
from tests import test_web_server as tws
from tests.test_pluto_gain import pluto, tx_stopped
from tests.test_web_layout import App, EdgePage, edge_available, open_app

UI_WEB = tws.UI_WEB
MHZ = 1_000_000
GRID = [100 * MHZ, 2400 * MHZ, 5000 * MHZ]


class PlutoGainServerTest(unittest.TestCase):
    post = tws.WebServerTest.post
    get = tws.WebServerTest.get
    tearDown = tws.WebServerTest.tearDown

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        profile = load_profile(GIGAST_PROFILE_PATH)
        sim = _SimulatorBackend(profile)
        sim.speed = 0.05
        self.pl = pluto()
        self.lib = FakeIioLib(self.pl)
        pd._cache = pd._SerialCache()
        self.api = LocalApi(Instrument({"simulator": sim, "plutosdr": _PlutoSdrBackend(lib_factory=lambda: self.lib)},
                                       profile=profile, data_root=root / "data", cal_root=root / "cal"))
        self.api._inst._pluto_gain_grid = list(GRID)
        self.srv = tws._Server(self.api)

    def state(self):
        return json.loads(self.get("/api/v1/state")[1])

    def test_state_carries_the_same_views_as_qt(self):
        self.assertEqual(self.post("connect", {"backend": "plutosdr", "port": FAKE_URI})[0], 200)
        s = self.state()
        self.assertEqual(s["ui"]["pluto"]["gain_cal_mark"], PLUTO_LABELS["gain_cal_none"])
        self.assertEqual(s["ui"]["pluto"]["gain_cal_tip"], PLUTO_LABELS["gain_cal_none_tip"])
        self.assertIsNone(s["ui"]["pluto"]["gain_reason"])
        st, r = self.post("cal_open", {"kind": "pluto_gain"})
        self.assertEqual(st, 200)
        s = self.state()
        ui = s["ui"]["cal"]
        self.assertEqual(ui, json.loads(json.dumps(cal_view.view(s))))
        self.assertEqual(ui["title"], qt_texts.MENU_ITEMS["cal_pluto_gain"].rstrip("…"))
        self.assertEqual(len(ui["pluto_rows"]), len(GRID))
        self.assertIsNone(ui["next_reason"])
        st, r = self.post("cal_set", {"ext_atten_db": 35.0})
        self.assertEqual(st, 200, r)
        self.assertEqual(self.post("cal_measure")[0], 200)
        end = time.monotonic() + 60
        while self.state()["tasks"]:
            self.assertLess(time.monotonic(), end)
            time.sleep(0.1)
        s = self.state()
        self.assertEqual([r["cells"][4] for r in s["ui"]["cal"]["pluto_rows"]], [cal_view.C["st_measured"]] * len(GRID))
        self.assertTrue(tx_stopped(self.pl))
        self.assertEqual(self.post("cal_save")[0], 200)
        s = self.state()
        self.assertEqual(s["ui"]["pluto"]["gain_cal_mark"], "")
        self.assertEqual(s["ui"]["pluto"], json.loads(json.dumps(screen_pluto.pluto_view(s))))

    def test_tables_and_files(self):
        t = json.loads(self.get("/api/v1/texts")[1])
        self.assertEqual(t["CAL_MENU_OF_KIND"]["pluto_gain"], "cal_pluto_gain")
        self.assertNotIn("cal_pluto_gain", t["MENU_NOT_YET"])
        self.assertEqual(t["ERRORS"]["cal_pluto_no_signal"], qt_texts.ERRORS["cal_pluto_no_signal"])
        self.assertEqual(t["ERRORS"]["plutosdr_tg_conditions_locked"], qt_texts.ERRORS["plutosdr_tg_conditions_locked"])
        menu = (UI_WEB / "app_menu.js").read_text(encoding="utf-8")
        self.assertIn('this.openCal("pluto_gain")', menu)
        cal = (UI_WEB / "app_cal.js").read_text(encoding="utf-8")
        self.assertIn("v.kind === 'pluto_gain'", cal)
        self.assertIn("ext_atten_db", cal)
        html = (UI_WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-mark="pluto_gain_cal"', html)
        self.assertIn(':disabled="!!plutoView.gain_reason"', html)


@unittest.skipUnless(edge_available(), "Microsoft Edge が無い")
class PlutoGainBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = App(lan=False, pluto=True)
        cls.app.api._inst._pluto_gain_grid = list(GRID)
        try:
            cls.page = EdgePage()
        except Exception:
            cls.app.close()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.page.close()
        cls.app.close()

    def test_measure_check_save(self):
        page, app = self.page, self.app
        found = []
        for w, h in ((1280, 720), (1920, 1080)):
            open_app(page, app, w, h)
            page.ev("gsApp.tab = 'trace'; 1")
            page.settle()
            self.assertEqual(page.ev("document.querySelector('[data-mark=pluto_gain_cal]').textContent.trim()"),
                             PLUTO_LABELS["gain_cal_none"])
            self.assertEqual(page.ev("document.querySelector('[data-mark=pluto_gain_cal]').title"),
                             PLUTO_LABELS["gain_cal_none_tip"])
            page.ev("gsApp.trigger('cal_pluto_gain'); 1")
            page.wait("!!document.querySelector('.calwin')", "校正の画面")
            page.settle()
            self.assertEqual(page.ev("document.querySelector('.calwin h3').textContent"),
                             qt_texts.MENU_ITEMS["cal_pluto_gain"].rstrip("…"))
            self.assertEqual(page.ev("[...document.querySelectorAll('.calwin .cal-box')].map((b) => b.textContent.trim())"),
                             list(qt_texts.CAL_WIRING["pluto_gain"]))
            found += page.problems(".modal.calwin", f"{w}×{h} 準備")
            btns = "[...document.querySelectorAll('.calwin .cal-btns button')]"
            page.ev(f"{btns}[1].click(); 1")  # 次へ
            page.wait("gsApp.calWin.page === 1 && !!document.querySelector('.calwin .cal-pluto')", "測る")
            page.settle()
            self.assertEqual(page.ev("document.querySelectorAll('.calwin .cal-pluto tbody tr').length"), len(GRID))
            if not app.call("get_state")["calibration_work"]["unsaved"]:
                page.ev("[...document.querySelectorAll('.calwin .cal-side button')].find(b => b.textContent.trim() === "
                        f"{json.dumps(cal_view.C['start'])}).click(); 1")
                page.wait("gsApp.calView && gsApp.calView.measuring", "測り始める")
                page.wait("!!document.querySelector('.calwin .cal-progress')", "進み具合")
                page.wait("gsApp.calView && !gsApp.calView.measuring", "測り終わる", timeout=90)
                page.settle()
            self.assertEqual(page.ev("[...document.querySelectorAll('.calwin .cal-pluto tbody tr td:last-child')]"
                                     ".map((t) => t.textContent.trim())"), [cal_view.C["st_measured"]] * len(GRID))
            found += page.problems(".modal.calwin", f"{w}×{h} 測る")
            page.ev(f"{btns}[1].click(); 1")  # 次へ（確かめる）
            page.wait("gsApp.calWin.page === 2 && !!document.querySelector('.calwin .cal-graph')", "確かめる")
            page.settle()
            self.assertEqual(page.ev("document.querySelector('.calwin .cal-items tbody td').textContent.trim()"),
                             cal_view.C["item_pluto_gain_max"])
            found += page.problems(".modal.calwin", f"{w}×{h} 確かめる")
            # 閉じる → 測った周波数が失われることを聞く（Qt と同じ文）→ やめる（窓は残る）
            page.ev("gsApp.calWin.page = 0; gsApp.calClose(); 1")
            page.wait("!!gsApp.confirmBox", "閉じるときに聞く")
            self.assertEqual(page.ev("gsApp.confirmBox.text"), cal_view.w("lost_freqs", n=len(GRID)))
            page.ev("gsApp.answerConfirm(false); 1")
            page.wait("!gsApp.confirmBox && !!gsApp.calWin", "やめる")
        # 保存すると印が消える
        page.ev("gsApp.calWin.page = 2; 1")
        page.settle()
        page.ev("[...document.querySelectorAll('.calwin .cal-btns button')][1].click(); 1")  # 保存
        page.wait("gsApp.calWin && gsApp.calWin.page === 3", "保存")
        page.ev("gsApp.calClose(); 1")
        page.wait("!gsApp.calWin", "閉じた")
        page.ev("gsApp.tab = 'trace'; 1")
        page.wait("!document.querySelector('[data-mark=pluto_gain_cal]')", "印が消える")
        self.assertEqual(sorted(set(found)), [])
        self.assertEqual(page.errors, [])


if __name__ == "__main__":
    unittest.main()
