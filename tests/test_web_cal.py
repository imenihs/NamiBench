"""Web の校正の画面（設計K §4.1〜§4.5。ui_web/app_cal.js）。Qt の cal_dialog と同じ中身・同じ動き（J §5）。

- 状態の ui.cal に、Qt と同じ cal_view.view の中身が付く（開いていなければ None）
- 旧校正ファイルはアップロードの番号で読み込む（Web の口はパスを受け取らない: A §4.3.2 の 3）
- 語の表（CAL_TEXTS など）が届き、メニューの校正の項目は「まだ使えません」でない
- ブラウザで: メニューから開く → 次へ → 測る（ボタン）→ 一覧に「測った」→ 閉じると聞く（保存せずに閉じる）。Edge が
  無いとき・Vue を CDN から読めないときは飛ばす
"""
from __future__ import annotations

import json
import time
import unittest
import urllib.request

from pygigast.ui_qt import cal_view
from pygigast.ui_qt import texts as qt_texts
from tests import test_web_server as tws
from tests.test_level import build_legacy_bytes
from tests.test_web_layout import App, EdgePage, edge_available, open_app
from tests.test_web_settings import _Base

UI_WEB = tws.UI_WEB


class CalServerTest(_Base):
    def put(self, name, data: bytes):
        req = urllib.request.Request(f"{self.srv.base}/api/v1/uploads?name={urllib.request.quote(name)}", data=data,
                                     method="PUT", headers={"Content-Type": "application/octet-stream"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())

    def test_state_carries_the_same_view_as_qt(self):
        self.assertIsNone(self.state()["ui"]["cal"])
        self.connect_and_sweep(400_000_000, 600_000_000)
        st, r = self.post("cal_open", {"kind": "freq"})
        self.assertEqual(st, 200)
        self.assertEqual(r["calibration_work"]["kind"], "freq")
        s = self.state()
        ui = s["ui"]["cal"]
        self.assertEqual(ui, json.loads(json.dumps(cal_view.view(s))))
        self.assertEqual(ui["title"], qt_texts.MENU_ITEMS["cal_freq"].rstrip("…"))
        self.assertEqual(len(ui["points"]), 121)
        self.assertEqual(self.post("cal_close")[0], 200)
        self.assertIsNone(self.state()["ui"]["cal"])

    def test_legacy_by_upload_and_no_paths(self):
        self.connect_and_sweep(400_000_000, 600_000_000)
        self.post("cal_open", {"kind": "legacy"})
        st, r = self.post("cal_legacy_load", {"path": "C:/x.txt"})
        self.assertEqual(r["error"]["code"], "path_not_allowed")
        up = self.put("gigast.txt", build_legacy_bytes())
        st, r = self.post("cal_legacy_load", {"upload_id": up["upload_id"]})
        self.assertEqual(st, 200, r)
        self.assertEqual(r["calibration_work"]["legacy"]["file_name"], "gigast.txt")
        st, r = self.post("cal_save")
        self.assertEqual(st, 200, r)
        self.assertEqual(self.state()["calibration"]["source"]["kind"], "legacy_file")

    def test_tables_and_files(self):
        t = json.loads(self.get("/api/v1/texts")[1])
        for name in ("CAL_TEXTS", "CAL_MENU_OF_KIND", "CAL_WIRING", "CAL_ERRORS"):
            self.assertIn(name, t)
        for code in ("cal_freq", "cal_rbw", "cal_nf", "cal_legacy"):
            self.assertNotIn(code, t["MENU_NOT_YET"])
        self.assertEqual(t["ERRORS"]["cal_no_signal"], qt_texts.ERRORS["cal_no_signal"])
        html = (UI_WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn('<script src="app_cal.js"></script>', html)
        self.assertIn("<gs-cal", html)
        menu = (UI_WEB / "app_menu.js").read_text(encoding="utf-8")
        for kind in ("freq", "rbw", "nf", "legacy"):
            self.assertIn(f'this.openCal("{kind}")', menu)


@unittest.skipUnless(edge_available(), "Microsoft Edge が無い")
class CalBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = App()
        try:
            cls.page = EdgePage()
        except Exception:
            cls.app.close()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.page.close()
        cls.app.close()

    def test_measure_and_close_without_saving(self):
        page, app = self.page, self.app
        open_app(page, app, 1280, 720)
        dev = app.api._inst._device
        page.ev("gsApp.trigger('cal_freq'); 1")
        page.wait("!!document.querySelector('.calwin')", "校正の画面")
        page.settle()
        self.assertEqual(page.ev("document.querySelector('.calwin h3').textContent"),
                         qt_texts.MENU_ITEMS["cal_freq"].rstrip("…"))
        btns = "[...document.querySelectorAll('.calwin .cal-btns button')]"
        page.ev(f"{btns}[1].click(); 1")  # 次へ
        page.wait("gsApp.calWin.page === 1", "測る")
        dev.config.signals = [(10_000_000, -10.0)]
        page.ev("[...document.querySelectorAll('.calwin .cal-side button')].find(b => b.textContent.trim() === "
                f"{json.dumps(cal_view.C['measure'])}).click(); 1")
        page.wait("gsApp.calView && gsApp.calView.points[0].status === 'measured'", "測った", timeout=30)
        page.settle()
        self.assertEqual(page.ev("document.querySelector('.calwin .cal-points tbody tr td:last-child').textContent.trim()"),
                         cal_view.C["st_measured"])
        self.assertEqual(page.problems(".modal.calwin", "校正 測る"), [])
        # 閉じる → 聞く（3つ目のボタン「保存せずに閉じる」）
        page.ev(f"{btns}[2].click(); 1")
        page.wait("!!gsApp.confirmBox", "閉じるときに聞く")
        self.assertEqual(page.ev("gsApp.confirmBox.text"), cal_view.w("lost_points", n=1))
        page.ev("[...document.querySelectorAll('.modal.confirm button')].find(b => b.textContent.trim() === "
                f"{json.dumps(cal_view.C['do_close_discard'])}).click(); 1")
        page.wait("!gsApp.calWin", "閉じた")
        end = time.monotonic() + 10
        while app.call("get_state")["calibration_work"] is not None:
            self.assertLess(time.monotonic(), end)
            time.sleep(0.05)
        self.assertEqual(page.errors, [])


if __name__ == "__main__":
    unittest.main()
