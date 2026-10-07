"""Qt の画面: SG のステップ掃引（設計L §1.3）と内部の漏れの印（設計L §1.4）。

- 信号源のタブの「ステップ掃引」: 欄・見積もり・始めるボタンの使えない理由（SG モードでない）・直した値の知らせ・
  始める → 処理の帯・SG の欄は理由付きで変えられない → 終わりの知らせと結果のトレース（固定・凡例）・
  全部重ねるときの段ごとの線（グラフと PNG）
- 内部の漏れの印: グラフの上端の ▼（表示のタブのチェックで消せる）・マウスの位置の読み値・ピークの一覧の列
- 崩れ（高さ・行のそろい・文字の切れ）を数えて 0 件（1280×720・1920×1080）
"""
from __future__ import annotations

import numpy as np

from pygigast.ui_qt import png_export, texts
from pygigast.ui_qt.mark_bands import LEAK_SYMBOL
from tests.qt_layout_check import problems
from tests.test_ui_qt_support import UiBase

MHZ = 1_000_000
L = texts.LABELS


class StepUiTest(UiBase):
    size = (1280, 720)

    def setUp(self):
        super().setUp()
        self.connect_and_sweep(300 * MHZ, 2100 * MHZ)
        self.panel = self.win.source_tab.step
        self.win.show_part("tab-source")

    def wait_idle(self):
        self.wait(lambda: not (self.win.state.get("step_sweep") or {}).get("running")
                  and not self.state()["step_sweep"]["running"], timeout=30, what="ステップ掃引の終わり")
        self.pump(0.3)

    def test_reason_until_sg_mode(self):
        self.assertFalse(self.panel.run.isEnabled())
        self.assertIn(texts.ERRORS["step_sweep_needs_sg"], self.panel.run.toolTip())
        self.cmd("set_mode", mode="sg", sg_hz=500 * MHZ)
        self.wait(lambda: self.panel.run.isEnabled(), what="SG モードで押せる")
        self.assertEqual(self.panel.estimate.text().split(" × ")[0], "41 段")

    def test_fields_send_and_show_adjustments(self):
        self.panel.f_start.edit.setText("0.5")
        self.panel.f_start.edit.committed.emit("0.5")
        self.wait(lambda: self.state()["step_sweep"]["start_hz"] == 1 * MHZ, what="SG 開始を範囲に収める")
        self.wait(lambda: "SG の範囲" in self.panel.f_start.note.text(), what="直した知らせ")
        self.panel.f_step.edit.committed.emit("50M")
        self.wait(lambda: self.state()["step_sweep"]["step_hz"] == 50 * MHZ, what="刻み")
        i = self.panel.combine.findData("all")
        self.panel.combine.setCurrentIndex(i)
        self.panel.combine.activated.emit(i)
        self.wait(lambda: self.state()["step_sweep"]["combine"] == "all", what="重ね方")
        i = self.panel.trace.findData(3)
        self.panel.trace.activated.emit(i)
        self.wait(lambda: self.state()["step_sweep"]["trace"] == 3, what="結果のトレース")
        self.panel.per_step.setValue(4)
        self.panel.per_step.editingFinished.emit()
        self.wait(lambda: self.state()["step_sweep"]["per_step"] == 4, what="1段の回数")
        self.wait(lambda: self.panel.estimate.text().endswith("× 4 回"), what="見積もり")

    def test_run_all_draws_step_lines(self):
        self.cmd("set_mode", mode="sg", sg_hz=500 * MHZ)
        self.cmd("set_step_sweep", start_hz=400 * MHZ, stop_hz=700 * MHZ, step_hz=100 * MHZ, per_step=1,
                 combine="all", trace=2)
        self.wait(lambda: self.panel.run.isEnabled(), what="押せる")
        self.panel.run.click()
        self.wait(lambda: self.state()["step_sweep"]["running"] or self.state()["traces"][1]["action"] == "fixed",
                  what="始まる")
        self.wait_idle()
        self.wait(lambda: "ステップ掃引が終わりました（4/4 段）" in self.win.st_note.text(), what="終わりの知らせ")
        self.assertTrue(self.win.source_tab.f_sg.isEnabled())
        p = self.win.traces.live_payload(2)
        self.assertIsNotNone(p)
        self.assertEqual(len(p["steps"]["values"]), 4)
        xs, ys = self.win.traces.live[2]["steps"].getData()
        self.assertEqual(int(np.isnan(ys).sum()), 4)  # 段ごとに値なしで区切った1本
        legends = [s for _c, s, _p in self.win.traces.legend_entries()]
        self.assertTrue(any("ステップ掃引・全部重ねる 4/4 段" in s for s in legends), legends)
        # PNG にも段ごとの線（画面と同じ中身）
        data = self.win.png_data()
        tr = next(t for t in data["traces"] if t.get("number") == 2)
        self.assertEqual(len(tr["steps"]["values"]), 4)
        png = png_export.render_png(data, (800, 500))
        self.assertTrue(png.startswith(b"\x89PNG"))

    def test_running_locks_sg_and_image_reject(self):
        self.cmd("set_mode", mode="sg", sg_hz=500 * MHZ)
        self.cmd("set_step_sweep", start_hz=1000 * MHZ, stop_hz=1500 * MHZ, step_hz=1 * MHZ, combine="max", trace=2)
        r = self.cmd("step_sweep_start")
        try:
            self.wait(lambda: (self.win.state.get("step_sweep") or {}).get("running"), what="している間")
            self.assertFalse(self.win.source_tab.f_sg.isEnabled())
            self.assertFalse(self.panel.run.isEnabled())
            self.assertFalse(self.panel.f_start.isEnabled())
            self.assertIn(texts.REASON_TEXTS["step_running"], self.panel.f_start.toolTip())
            self.assertIn(texts.REASON_TEXTS["image_reject_step"], self.win.trace_tab.image_reject.toolTip())
            self.assertFalse(self.win.trace_tab.image_reject.isEnabled())
            self.wait(lambda: self.win.task_panel.isVisible(), what="処理の帯")
            self.assertIn("ステップ掃引", self.win.task_panel._rows[r["task_id"]].label.text())
        finally:
            self.cmd("cancel_task", task_id=r["task_id"])
        self.wait_idle()
        self.wait(lambda: "ステップ掃引をやめました" in self.win.st_note.text(), what="やめた知らせ")
        self.assertTrue(self.win.trace_tab.image_reject.isEnabled())

    def test_leak_marks_and_peak_list(self):
        self.cmd("set_mode", mode="sg", sg_hz=500 * MHZ)
        # イメージ除去「スピード」（初期値）では b3 で動く 956MHz のスプリアスは消えるので印は2つ
        self.wait(lambda: len(self.win.mark_bands.leaks) == 2, what="漏れの印")
        items = [it for it, _m in self.win.mark_bands.leaks]
        self.assertEqual({it.textItem.toPlainText() for it in items}, {LEAK_SYMBOL})
        self.assertEqual([m["freq_hz"] for _it, m in self.win.mark_bands.leaks], [1480 * MHZ, 1980 * MHZ])
        # なし（b3=1 だけ）では 946MHz にも印
        self.cmd("set_sweep", image_reject="off")
        self.wait(lambda: [m["freq_hz"] for _it, m in self.win.mark_bands.leaks] == [946 * MHZ, 1480 * MHZ,
                                                                                      1980 * MHZ], what="946MHz の印")
        self.cmd("set_sweep", image_reject="fast")
        self.wait(lambda: len(self.win.mark_bands.leaks) == 2, what="スピードに戻す")
        # 消す（表示のタブ）→ 印が無くなる
        self.win.display_tab.leak_marks.setChecked(False)
        self.wait(lambda: not self.win.mark_bands.leaks, what="印を消す")
        self.assertIs(self.state()["view"]["leak_marks"], False)
        self.win.display_tab.leak_marks.setChecked(True)
        self.wait(lambda: len(self.win.mark_bands.leaks) == 2, what="印を戻す")
        # マウスの位置の読み値（印の近く）
        mr = self.win.mouse_reading
        self.assertEqual(len(mr.leaks_fn()), 2)
        # ピークの一覧の列
        n0 = self.state()["run"]["sweep_count"]
        self.cmd("run", mode="single")
        self.wait(lambda: self.state()["run"]["sweep_count"] > n0 and self.state()["run"]["state"] == "stopped",
                  what="単発")
        self.pump(0.3)
        tab = self.win.marker_tab
        tab.pk_trace.setCurrentIndex(0)
        tab._find_peaks()
        self.wait(lambda: tab.pk_table.rowCount() >= 3, what="ピークの一覧")
        rows = {tab.pk_table.item(i, 0).text(): tab.pk_table.item(i, 4) for i in range(tab.pk_table.rowCount())}
        self.assertEqual(rows["1480"].text(), L["peak_leak"])
        self.assertIn("SG + 1480 MHz", rows["1980"].toolTip())
        self.assertEqual(rows["500"].text(), "")
        # SA に戻すと印は無い
        self.cmd("set_mode", mode="sa")
        self.wait(lambda: not self.win.mark_bands.leaks, what="SA では印なし")


class StepLayoutTest(UiBase):
    """ステップ掃引の欄（している間も）・漏れの印のあるピークの一覧・表示のタブを、決めた解像度で数える。"""

    def check_size(self, size):
        self.win.resize(*size)
        self.connect_and_sweep(300 * MHZ, 2100 * MHZ)
        self.cmd("set_mode", mode="sg", sg_hz=500 * MHZ)
        found = []
        self.win.show_part("tab-source")
        self.pump(0.3)
        found += problems(self.win, f"{size} 信号源")
        self.cmd("set_step_sweep", start_hz=1000 * MHZ, stop_hz=1500 * MHZ, step_hz=1 * MHZ, combine="all",
                 trace=2)
        self.cmd("set_step_sweep", step_hz=5 * MHZ)
        r = self.cmd("step_sweep_start")
        self.wait(lambda: self.win.task_panel.isVisible(), what="処理の帯")
        self.pump(0.2)
        found += problems(self.win, f"{size} 信号源（している間）")
        self.cmd("cancel_task", task_id=r["task_id"])
        self.wait(lambda: not self.state()["step_sweep"]["running"], what="やめた")
        n0 = self.state()["run"]["sweep_count"]
        self.cmd("run", mode="single")
        self.wait(lambda: self.state()["run"]["sweep_count"] > n0 and self.state()["run"]["state"] == "stopped",
                  what="単発")
        self.win.show_part("tab-marker")
        self.win.marker_tab._find_peaks()
        self.wait(lambda: self.win.marker_tab.pk_table.rowCount() >= 3, what="ピークの一覧")
        self.pump(0.2)
        found += problems(self.win, f"{size} マーカー（漏れの印）")
        self.win.show_part("tab-view")
        self.pump(0.2)
        found += problems(self.win, f"{size} 表示")
        self.assertEqual(found, [])

    def test_1280x720(self):
        self.check_size((1280, 720))

    def test_1920x1080(self):
        self.check_size((1920, 1080))
