"""上段の掃引の進み具合（設計J §4.13）の Qt の画面の試験。

- 連続・単発・停止の真上の名前の行（小の段）に、細い棒と割合。見積もりが 1 秒を超える掃引の間だけ出す
- 出しても隠してもボタンは動かない（場所を空けたまま）。崩れを数える試験（qt_layout_check）は 0 件
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from PySide6.QtCore import QPoint, QRect

from pygigast.ui_qt import sizes
from tests.qt_layout_check import problems
from tests.test_ui_qt_support import UiBase

ROOT = Path(__file__).resolve().parents[1]


def grect(w) -> QRect:
    return QRect(w.mapToGlobal(QPoint(0, 0)), w.size())


class SizeValuesTest(unittest.TestCase):
    def test_web_has_the_same_values(self):
        """棒の高さと割合の文字の幅は Qt（sizes.py）と Web（style.css の :root）で同じ値（U-09）。"""
        css = (ROOT / "pygigast" / "ui_web" / "style.css").read_text(encoding="utf-8")
        root = re.search(r":root\s*{(.*?)}", css, re.S).group(1)
        have = {m.group(1): int(m.group(2)) for m in re.finditer(r"(--[\w-]+):\s*(\d+)(?:px)?;", root)}
        self.assertEqual(have.get("--progress-bar-h"), sizes.PROGRESS_BAR_H)
        self.assertEqual(have.get("--progress-text-ch"), sizes.PROGRESS_TEXT_CH)


class SweepProgressQtTest(UiBase):
    size = (1280, 720)

    def buttons(self):
        return [grect(b) for b in (self.win.btn_cont, self.win.btn_single, self.win.btn_stop)]

    def show_progress(self, done, total=100):
        self.win._on_event("sweep_progress", {"show": True, "done": done, "total": total})
        self.pump(0.2)

    def hide_progress(self):
        self.win._on_event("sweep_progress", {"show": False})
        self.pump(0.2)

    def check_layout(self, where):
        rb = self.win.run_box
        before = self.buttons()
        self.assertFalse(rb.bar.isVisible())
        self.assertEqual(problems(self.win, f"{where} 隠す"), [])
        for done in (37, 0, 100):
            self.show_progress(done)
            self.assertTrue(rb.bar.isVisible() and rb.text.isVisible())
            self.assertEqual(rb.text.text(), f"{done}%")
            self.assertEqual(rb.bar.value(), done)
            self.assertEqual(self.buttons(), before, f"{where} {done}%: ボタンが動いた")
            self.assertEqual(problems(self.win, f"{where} {done}%"), [])
            # 割合の文字は欄の中に収まる（「100%」でも切れない）
            self.assertLessEqual(rb.text.fontMetrics().horizontalAdvance(rb.text.text()), rb.text.width())
        # 名前の行は上段の欄の名前の行と同じ高さ・同じ上端（RBW の名前）。棒は行の中で縦の真ん中
        name = self.win.rbw_field.name_label
        self.assertEqual(grect(rb.progress_row).top(), grect(name).top())
        self.assertEqual(rb.progress_row.height(), sizes.H_S)
        self.assertEqual(rb.bar.height(), sizes.PROGRESS_BAR_H)
        self.assertLessEqual(abs(grect(rb.bar).center().y() - grect(rb.progress_row).center().y()), 1)
        # 行の幅はボタンの並びの幅（左端と右端がそろう）
        row = grect(rb.progress_row)
        self.assertEqual((row.left(), row.right()), (before[0].left(), before[-1].right()))
        self.hide_progress()
        self.assertFalse(rb.bar.isVisible() or rb.text.isVisible())
        self.assertEqual(self.buttons(), before)

    def test_layout_and_buttons_do_not_move(self):
        self.cmd("connect", backend="simulator", serial="SIM00001")
        self.check_layout("SA")
        # TG では右に「基準を取る」が並ぶ
        self.cmd("set_mode", mode="tg")
        self.wait(lambda: self.win.ref_quick.isVisible(), what="基準を取る")
        self.pump(0.2)
        self.check_layout("TG")

    def test_shown_only_while_a_long_screen_is_measured(self):
        seen = []
        rb = self.win.run_box
        orig = rb.set_progress
        rb.set_progress = lambda v: (seen.append(dict(v)), orig(v))[1]
        # 見積もり 1 秒以下（100〜200MHz）: 出さない
        self.connect_and_sweep(100_000_000, 200_000_000)
        self.assertLessEqual(self.state()["sweep"]["estimated_sweep_s"], 1.0)
        self.assertFalse(any(v["visible"] for v in seen))
        self.assertFalse(rb.bar.isVisible())
        # 見積もり 5.7 秒（50MHz〜2GHz・正確。シミュレータは見積もりの 1/5 の時間）: 測る間だけ出す
        self.api._inst._backends["simulator"].speed = 0.2
        self.cmd("disconnect")
        self.cmd("connect", backend="simulator", serial="SIM00001")
        self.cmd("set_sweep", start_hz=50_000_000, stop_hz=2_000_000_000, image_reject="accurate")
        self.assertGreater(self.state()["sweep"]["estimated_sweep_s"], 1.0)
        n0 = self.state()["run"]["sweep_count"]
        self.cmd("run", mode="single")
        self.wait(lambda: rb.bar.isVisible(), timeout=10, what="進み具合が出る")
        self.assertRegex(rb.text.text(), r"^\d{1,3}%$")
        self.wait(lambda: self.state()["run"]["state"] == "stopped" and self.state()["run"]["sweep_count"] > n0,
                  timeout=20, what="単発の掃引")
        self.wait(lambda: not rb.bar.isVisible(), what="止まったら隠す")
        shown = [v["percent"] for v in seen if v["visible"]]
        self.assertTrue(shown)
        self.assertEqual(shown, sorted(shown))


class SweepProgressQtWideTest(SweepProgressQtTest):
    size = (1920, 1080)

    def test_shown_only_while_a_long_screen_is_measured(self):
        pass  # 解像度によらない（1280×720 で確かめる）
