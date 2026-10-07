"""上段の掃引の進み具合（設計J §4.13）の Web の画面の試験（Qt の test_ui_qt_sweep_progress と同じことを確かめる）。

- 連続・単発・停止の真上の名前の行（小の段）に、細い棒と割合。見積もりが 1 秒を超える掃引の間だけ出す
- 出しても隠してもボタンは動かない（場所を空けたまま）。崩れを数える試験（test_web_layout の台本）は 0 件
- 知らせはサーバーが Qt と同じ screen_model.sweep_progress_view で形にして送る（途中から繋いだ画面にも送る）
"""
from __future__ import annotations

import time
import unittest

from tests.test_web_layout import SIZES, App, EdgePage, edge_available, open_app

RUN_RECTS = ("[...document.querySelectorAll('.runbtns .run-btn')].map((b) => { const r = b.getBoundingClientRect();"
             " return [r.left, r.top, r.width, r.height]; })")
HEAD = "document.querySelector('.sweep-progress')"


@unittest.skipUnless(edge_available(), "Microsoft Edge が無い")
class WebSweepProgressTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = App(lan=False)
        try:
            cls.page = EdgePage()
        except Exception:
            cls.app.close()
            raise
        cls.hub = cls.app.srv.server.config.app.hub

    @classmethod
    def tearDownClass(cls):
        cls.page.close()
        cls.app.close()

    def send(self, payload: dict) -> None:
        """コアの知らせと同じ道でサーバーへ渡す（制御スレッドから届いたのと同じ）。"""
        self.hub._from_control_thread("sweep_progress", payload)

    def head(self) -> dict:
        return self.page.ev(f"""(() => {{
          const h = {HEAD}, t = h.querySelector('.sp-text'), f = h.querySelector('.sp-fill'), b = h.querySelector('.sp-bar');
          const r = h.getBoundingClientRect(), rb = b.getBoundingClientRect();
          const name = document.querySelector('.tg-ctl .field .field-head').getBoundingClientRect();
          const btns = document.querySelector('.runbtns').getBoundingClientRect();
          return {{visible: getComputedStyle(h).visibility === 'visible', text: t.textContent.trim(),
                  fill: f.getBoundingClientRect().width / Math.max(1, b.clientWidth),
                  clipped: t.scrollWidth > t.clientWidth, top: r.top, height: r.height, nameTop: name.top,
                  left: r.left, right: r.right, btnLeft: btns.left, btnRight: btns.right,
                  barMid: rb.top + rb.height / 2, mid: r.top + r.height / 2, barH: rb.height}};
        }})()""")

    def check(self, where: str) -> None:
        self.page.ev("gsApp.tab = 'trace'; 1")
        self.page.settle()
        self.send({"show": False})
        self.page.wait("!gsApp.sweepProgress.visible", "隠す")
        self.page.settle()
        before = self.page.ev(RUN_RECTS)
        self.assertFalse(self.head()["visible"])
        self.assertEqual(self.page.problems(".app", f"{where} 隠す"), [])
        for done in (37, 0, 100):
            self.send({"show": True, "done": done, "total": 100})
            self.page.wait(f"gsApp.sweepProgress.visible && gsApp.sweepProgress.percent === {done}", f"{done}%")
            self.page.settle()
            h = self.head()
            self.assertTrue(h["visible"])
            self.assertEqual(h["text"], f"{done}%")
            self.assertFalse(h["clipped"])
            self.assertAlmostEqual(h["fill"], done / 100, delta=0.02)
            self.assertEqual(self.page.ev(RUN_RECTS), before, f"{where} {done}%: ボタンが動いた")
            self.assertEqual(self.page.problems(".app", f"{where} {done}%"), [])
            # 名前の行は欄の名前の行と同じ上端・小の段の高さ。棒は縦の真ん中。行の幅はボタンの並びの幅
            self.assertAlmostEqual(h["top"], h["nameTop"], delta=0.5)
            self.assertAlmostEqual(h["height"], 20, delta=0.5)
            self.assertAlmostEqual(h["barH"], 6, delta=0.5)
            self.assertAlmostEqual(h["barMid"], h["mid"], delta=1)
            self.assertAlmostEqual(h["left"], h["btnLeft"], delta=0.5)
            self.assertAlmostEqual(h["right"], h["btnRight"], delta=0.5)
        self.send({"show": False})
        self.page.wait("!gsApp.sweepProgress.visible", "隠す")
        self.page.settle()
        self.assertFalse(self.head()["visible"])
        self.assertEqual(self.page.ev(RUN_RECTS), before)

    def check_size(self, w: int, h: int) -> None:
        open_app(self.page, self.app, w, h)
        self.check(f"{w}×{h} SA")
        try:
            self.app.call("set_mode", mode="tg")
            self.page.wait("!!(gsApp.refQuick.visible && document.querySelector('.refquick-state'))", "基準を取る")
            self.check(f"{w}×{h} TG")
        finally:
            self.app.call("set_mode", mode="sa")
        self.assertEqual(self.page.errors, [])

    def test_1280x720(self):
        self.check_size(*SIZES[0])

    def test_1920x1080(self):
        self.check_size(*SIZES[1])

    def test_new_page_gets_the_latest(self):
        # 途中から繋いだ画面にも、いまの進み具合を送る
        self.send({"show": True, "done": 42, "total": 100})
        time.sleep(0.3)
        open_app(self.page, self.app, *SIZES[0])
        self.page.wait("gsApp.sweepProgress.visible && gsApp.sweepProgress.percent === 42", "繋いだ直後の 42%")
        self.send({"show": False})
        self.page.wait("!gsApp.sweepProgress.visible", "隠す")

    def wait_stopped(self, n0: int) -> None:
        end = time.monotonic() + 30
        while True:
            st = self.app.call("get_state")
            if st["run"]["state"] == "stopped" and st["run"]["sweep_count"] > n0:
                return
            self.assertLess(time.monotonic(), end, "掃引が終わらない")
            time.sleep(0.05)

    def test_shown_only_while_a_long_screen_is_measured(self):
        sim = self.app.api._inst._backends["simulator"]
        open_app(self.page, self.app, *SIZES[0])
        self.page.ev("window.__sp = []; gsApp.$watch(() => gsApp.sweepProgress, (v) => window.__sp.push(v.visible)); 1")
        st0 = self.app.call("get_state")["sweep"]
        try:
            # 見積もり 1 秒以下（100〜200MHz）: 出さない
            self.app.call("set_sweep", start_hz=100_000_000, stop_hz=200_000_000)
            self.assertLessEqual(self.app.call("get_state")["sweep"]["estimated_sweep_s"], 1.0)
            n0 = self.app.call("get_state")["run"]["sweep_count"]
            self.app.call("run", mode="single")
            self.wait_stopped(n0)
            time.sleep(0.3)
            self.assertNotIn(True, self.page.ev("window.__sp"))
            # 見積もり 5.7 秒（50MHz〜2GHz・正確。シミュレータは見積もりの 1/5 の時間）: 測る間だけ出す
            sim.speed = 0.2
            self.app.call("disconnect")
            self.app.call("connect", backend="simulator", serial="SIM00001")
            self.app.call("set_sweep", start_hz=50_000_000, stop_hz=2_000_000_000, image_reject="accurate")
            self.assertGreater(self.app.call("get_state")["sweep"]["estimated_sweep_s"], 1.0)
            n0 = self.app.call("get_state")["run"]["sweep_count"]
            self.app.call("run", mode="single")
            self.page.wait("gsApp.sweepProgress.visible", "進み具合が出る", timeout=10)
            self.assertRegex(self.head()["text"], r"^\d{1,3}%$")
            self.wait_stopped(n0)
            self.page.wait("!gsApp.sweepProgress.visible", "止まったら隠す")
            self.assertFalse(self.head()["visible"])
        finally:
            sim.speed = 0.05
            self.app.call("disconnect")
            self.app.call("connect", backend="simulator", serial="SIM00001")
            self.app.call("set_sweep", start_hz=st0["start_hz"], stop_hz=st0["stop_hz"], image_reject=st0["image_reject"])


if __name__ == "__main__":
    unittest.main()
