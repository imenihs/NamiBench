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
        self.assertEqual(len(ui["points"]), 132)  # band の境目の 11 か所は2行（K §4.2.1）
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


    def test_band_edge_rows_show_band_and_sg_frequency(self):
        # band の境目は2行。どちらの帯の欄か・SG に出させる周波数が行に出る（Qt と同じ: K §4.2.1）
        page, app = self.page, self.app
        for w, h in ((1280, 720), (1920, 1080)):
            open_app(page, app, w, h)
            page.ev("gsApp.openCal('freq'); 1")
            page.wait("!!document.querySelector('.calwin')", "校正の画面")
            page.ev("gsApp.calNext(); 1")
            page.wait("gsApp.calWin.page === 1 && !!document.querySelector('.calwin .cal-points tbody tr')", "測る")
            app.call("cal_set", current=11)
            page.wait("gsApp.calView && gsApp.calView.points[11].current", "上の帯の行")
            page.settle()
            heads = page.ev("[...document.querySelectorAll('.calwin .cal-points thead th')].map(e => e.textContent.trim())")
            self.assertEqual(heads[:3], [cal_view.C["col_freq"], cal_view.C["col_band"], cal_view.C["col_sg_freq"]])
            rows = page.ev("[...document.querySelectorAll('.calwin .cal-points tbody tr')]"
                           ".map(r => [...r.querySelectorAll('td')].slice(0, 3).map(e => e.textContent.trim()))")
            self.assertEqual(len(rows), 132)
            self.assertEqual(rows[9], ["900", "", "900"])
            self.assertEqual(rows[10], ["1000", cal_view.C["band_lower"], "999.5"])
            self.assertEqual(rows[11], ["1000", cal_view.C["band_upper"], "1000.5"])
            self.assertIn("1000.500MHz", page.ev("document.querySelector('.calwin .cal-instruction').textContent"))
            self.assertEqual(page.problems(".modal.calwin", f"校正 測る {w}x{h}"), [])
            page.ev("gsApp.calNext(); 1")
            page.wait("gsApp.calWin.page === 2", "確かめる")
            page.settle()
            heads = page.ev("[...document.querySelectorAll('.calwin .cal-check-points thead th')].map(e => e.textContent.trim())")
            self.assertEqual(heads[1], cal_view.C["col_band"])
            self.assertEqual(page.problems(".modal.calwin", f"校正 確かめる {w}x{h}"), [])
            page.ev("gsApp.calClose(); 1")
            page.wait("!gsApp.calWin", "閉じた")
            # 本体の作業も閉じ終わるまで待つ（閉じるの命令が次の開くより後に届くと、次の試験の作業を閉じてしまう）
            end = time.monotonic() + 10
            while app.call("get_state")["calibration_work"] is not None:
                self.assertLess(time.monotonic(), end)
                time.sleep(0.05)
        self.assertEqual(page.errors, [])

    def test_opens_while_sweeping_and_keeps_the_scroll(self):
        # 掃引中は作業ができる前の状態（作業なし）が届く。それで窓を閉じない。手でスクロールした点の一覧を、状態が
        # 届くたびに選んでいる行へ戻さない（Qt と同じ: 一覧の中身が変わったときだけ寄せる）
        page, app = self.page, self.app
        open_app(page, app, 1280, 720)

        def sweeps(n):
            n0 = app.call("get_state")["run"]["sweep_count"]
            end = time.monotonic() + 30
            while app.call("get_state")["run"]["sweep_count"] < n0 + n:
                self.assertLess(time.monotonic(), end)
                time.sleep(0.05)

        app.call("run", mode="continuous")
        try:
            for _ in range(3):
                page.ev("gsApp.openCal('freq'); gsApp.onStateCal({ui: {cal: null}}); 1")
                page.wait("!!document.querySelector('.calwin')", "校正の画面")
                sweeps(2)
                page.settle()
                self.assertTrue(page.ev("!!document.querySelector('.calwin')"))
                page.ev("gsApp.calClose(); 1")
                page.wait("!gsApp.calWin", "閉じた")
            page.ev("gsApp.openCal('freq'); 1")
            page.wait("!!document.querySelector('.calwin')", "校正の画面")
            page.ev("gsApp.calNext(); 1")
            page.wait("gsApp.calWin.page === 1 && !!document.querySelector('.calwin .cal-points')", "測る")
            sweeps(2)
            page.settle()
            top = page.ev("(() => { const el = document.querySelector('.calwin .cal-points'); "
                          "el.scrollTop = el.scrollHeight; return el.scrollTop; })()")
            self.assertGreater(top, 100)
            sweeps(3)
            page.settle()
            self.assertEqual(page.ev("document.querySelector('.calwin .cal-points').scrollTop"), top)
        finally:
            app.call("stop")
            page.ev("gsApp.calWin && gsApp.calClose(); 1")
        self.assertEqual(page.errors, [])


    def test_round_trip_keeps_the_selected_point_visible(self):
        # 測る → 確かめる → 戻る では一覧が作り直されて先頭に戻る。状態が届かなくても選んでいる点の行へ寄せる
        page, app = self.page, self.app
        open_app(page, app, 1280, 720)
        page.ev("gsApp.openCal('freq'); 1")
        page.wait("!!document.querySelector('.calwin')", "校正の画面")
        app.call("cal_set", current=60)
        page.wait("gsApp.calView && gsApp.calView.points[60].current", "60 番の点")
        page.ev("gsApp.calNext(); 1")
        page.wait("gsApp.calWin.page === 1 && !!document.querySelector('.calwin .cal-points tr.sel')", "測る")
        page.settle()
        page.ev("gsApp.calNext(); 1")
        page.wait("gsApp.calWin.page === 2", "確かめる")
        page.settle()
        page.ev("gsApp.calBack(); 1")
        page.wait("gsApp.calWin.page === 1 && !!document.querySelector('.calwin .cal-points tr.sel')", "戻る")
        page.settle()
        seen = page.ev("(() => { const r = document.querySelector('.calwin .cal-points tr.sel').getBoundingClientRect(); "
                       "const b = document.querySelector('.calwin .cal-points').getBoundingClientRect(); "
                       "return r.top >= b.top - 1 && r.bottom <= b.bottom + 1; })()")
        self.assertTrue(seen)
        page.ev("gsApp.calClose(); 1")
        page.wait("!gsApp.calWin", "閉じた")

    def test_closed_elsewhere_right_after_opening(self):
        # 別の画面が開いた作業を Web でも開き、次の状態が届く前に別の画面が閉じても、Web の窓の扱いを残さない
        page, app = self.page, self.app
        open_app(page, app, 1280, 720)
        app.call("cal_open", kind="rbw")
        page.wait("!!(gsApp.ui && gsApp.ui.cal)", "作業のある状態")
        # 開くの命令が本体に届いてから閉じる（届く前に閉じると、後から届いた開くで作業が開き直り、試験の形にならない）
        page.ev("Promise.resolve(gsApp.openCal('rbw')).then(() => 1)")
        app.call("cal_close")
        page.wait("!gsApp.calWin", "窓の扱いが消える")
        app.call("cal_open", kind="rbw")
        page.wait("!!(gsApp.ui && gsApp.ui.cal)", "作業のある状態")
        page.settle()
        self.assertFalse(page.ev("!!document.querySelector('.calwin')"))  # 開いていない画面に窓は出ない
        app.call("cal_close")
        self.assertEqual(page.errors, [])


if __name__ == "__main__":
    unittest.main()
