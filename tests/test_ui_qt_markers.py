"""マーカーの表・キー・グラフの印・右のタブ「マーカー」を確かめる（設計G §3.2〜§3.5・J §4.3・§4.6）。"""
import unittest

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from tests.test_ui_qt_support import UiBase


class MarkerBase(UiBase):
    def setUp(self):
        super().setUp()
        self.connect_and_sweep()

    def markers(self):
        return self.state()["markers"]["markers"]

    def marker(self, mid):
        return next(m for m in self.markers() if m["id"] == mid)

    def table_rows(self):
        t = self.win.marker_panel.tree
        out = []
        for i in range(t.topLevelItemCount()):
            it = t.topLevelItem(i)
            out.append(it.text(0))
            for k in range(it.childCount()):
                out.append("  " + it.child(k).text(0))
        return out


class MarkerButtonsTest(MarkerBase):
    def test_peak_button_places_and_selects(self):
        self.win.marker_panel.btn_peak.click()
        self.wait(lambda: self.win.selected_marker == "M1", what="M1 を選ぶ")
        m = self.marker("M1")
        # 表示範囲の中の最大の点（G §3.2）
        import numpy as np
        vals = np.asarray(self.win.traces.live_payload(1)["values"], dtype=float)
        self.assertAlmostEqual(m["level"], float(np.nanmax(vals)), places=3)
        self.assertEqual(self.table_rows(), ["M1"])
        self.assertEqual(self.win.marker_panel.tree.currentItem().text(0), "M1")
        # グラフの印（点と名前）
        self.assertEqual(len(self.win.marker_layer.scatter.data), 1)
        # 次のピークで動く
        f0 = m["freq_hz"]
        self.win.marker_panel.btn_lower.click()
        self.wait(lambda: self.marker("M1")["freq_hz"] != f0, what="次に高いピークへ")

    def test_add_and_clear_with_confirm(self):
        self.win.marker_panel.btn_add.click()
        self.win.marker_panel.btn_add.click()
        self.wait(lambda: len(self.win.marker_snap["markers"]) == 2, what="2本")
        self.confirm_answer = False
        self.win.marker_panel.btn_clear.click()
        self.pump()
        self.assertEqual(len(self.markers()), 2)
        self.assertIn("2 本", self.confirm_asked[-1])
        self.confirm_answer = True
        self.win.marker_panel.btn_clear.click()
        self.wait(lambda: not self.markers(), what="全消去")
        self.wait(lambda: self.table_rows() == [], what="表も空")


class MarkerKeysTest(MarkerBase):
    def test_keys_p_brackets_arrows(self):
        self.win.plot.setFocus()
        self.pump(0.1)
        QTest.keyClick(self.win.plot, Qt.Key.Key_P)
        self.wait(lambda: self.win.selected_marker == "M1", what="P でピーク")
        f0 = self.marker("M1")["freq_hz"]
        step = self.state()["sweep"]["step_hz"]
        QTest.keyClick(self.win.plot, Qt.Key.Key_Right)
        self.wait(lambda: self.marker("M1")["freq_hz"] == f0 + step, what="→ で1点")
        QTest.keyClick(self.win.plot, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
        self.wait(lambda: self.marker("M1")["freq_hz"] == f0 + step - 10 * step, what="Shift+← で10点")
        f1 = self.marker("M1")["freq_hz"]
        QTest.keyClick(self.win.plot, Qt.Key.Key_BracketRight)
        self.wait(lambda: self.marker("M1")["freq_hz"] > f1, what="] で右の次のピーク")

    def test_delete_key_removes_selected_marker(self):
        self.win.marker_panel.btn_peak.click()
        self.wait(lambda: self.win.selected_marker == "M1", what="M1 を選ぶ")
        # 入力の欄にいる間の Delete は欄の文字を消すだけで、マーカーは消さない
        edit = self.win.marker_tab.freq.edit
        edit.setFocus()
        self.pump(0.1)
        QTest.keyClick(edit, Qt.Key.Key_Delete)
        self.pump(0.2)
        self.assertEqual(len(self.markers()), 1)
        # マーカーの表で選んで Delete
        tree = self.win.marker_panel.tree
        tree.setFocus()
        self.pump(0.1)
        QTest.keyClick(tree, Qt.Key.Key_Delete)
        self.wait(lambda: not self.markers() and self.win.selected_marker is None, what="表で Delete")
        # グラフで選んで Delete
        self.win.marker_panel.btn_peak.click()
        self.wait(lambda: self.win.selected_marker is not None, what="もう一度ピーク")
        self.win.plot.setFocus()
        self.pump(0.1)
        QTest.keyClick(self.win.plot, Qt.Key.Key_Delete)
        self.wait(lambda: not self.markers(), what="グラフで Delete")

    def test_m_key_at_mouse(self):
        vb = self.win.plot.getPlotItem().getViewBox()
        pos = self.win.plot.mapFromScene(vb.mapViewToScene(QPointF(700e6, -75)))
        QTest.mouseMove(self.win.plot.viewport(), pos)
        self.win.plot.setFocus()
        self.pump(0.1)
        # offscreen ではマウスの位置が動かないことがあるので、位置の読みを差し替えて確かめる
        from pygigast.ui_qt import main_window as mw
        orig = mw.QCursor.pos
        mw.QCursor.pos = staticmethod(lambda: self.win.plot.mapToGlobal(pos))
        try:
            QTest.keyClick(self.win.plot, Qt.Key.Key_M)
            self.wait(lambda: len(self.markers()) == 1, what="M でマーカー")
        finally:
            mw.QCursor.pos = orig
        self.assertAlmostEqual(self.marker("M1")["freq_hz"], 700e6, delta=5e6)

    def test_arrow_without_selection_says_so(self):
        self.win._marker_step(+1)
        self.assertIn("マーカーを選んで", self.win.st_note.text())


class MarkerGraphTest(MarkerBase):
    def test_click_places_marker_on_nearest_trace(self):
        self.cmd("set_trace", number=2, visible=True, action="fixed")
        self.pump()
        self.win._on_graph_clicked(1500e6, -30.0)
        self.wait(lambda: len(self.markers()) == 1, what="クリックで M")
        m = self.marker("M1")
        self.assertEqual(m["kind"], "M")
        self.assertAlmostEqual(m["freq_hz"], 1500e6, delta=2e6)

    def test_context_menu_marker_and_selected_actions(self):
        menu = self.win._context_menu(900e6, -50.0)
        acts = {a.objectName(): a for a in menu.actions()}
        acts["ctx_marker"].trigger()
        self.wait(lambda: self.win.selected_marker == "M1", what="ここにマーカー")
        menu = self.win._context_menu(900e6, -50.0)
        acts = {a.objectName(): a for a in menu.actions()}
        self.assertIn("ctx_m_center", acts)
        acts["ctx_m_center"].trigger()
        self.wait(lambda: self.state()["sweep"]["center_hz"] == self.marker("M1")["freq_hz"], what="Center に")


class MarkerTableTest(MarkerBase):
    def test_delta_ref_from_table_and_tsv(self):
        self.cmd("marker_add", kind="M", trace=1, freq_hz=100_000_000)
        self.cmd("marker_add", kind="M", trace=1, freq_hz=1_500_000_000)
        self.pump()
        cb = self.win.marker_panel._ref_combos["M2"]
        i = cb.findData("M1")
        cb.setCurrentIndex(i)
        cb.activated.emit(i)
        self.wait(lambda: self.marker("M2")["ref"] == "M1", what="基準を M1 に")
        self.pump()
        it = self.win.marker_panel._items["M2"]
        self.assertEqual(it.text(5), "+1400")          # Δ周波数 (MHz)
        self.assertTrue(it.text(6).startswith(("+", "-")))
        text = self.win.marker_panel.copy_tsv()
        lines = text.strip().split("\n")
        self.assertEqual(lines[0].split("\t")[:3], ["番号", "トレース", "周波数 (MHz)"])
        self.assertEqual(lines[2].split("\t")[:5], ["M2", "1", "1500", lines[2].split("\t")[3], "M1"])
        self.assertEqual(QGuiApplication.clipboard().text(), text)

    def test_row_menu_to_center_and_remove(self):
        self.cmd("marker_add", kind="M", trace=1, freq_hz=600_000_000)
        self.pump()
        row = next(r for r in self.win.marker_panel.rows if r["id"] == "M1")
        menu = self.win.marker_panel._row_menu(row)
        acts = {a.objectName(): a for a in menu.actions()}
        acts["row_center"].trigger()
        self.wait(lambda: self.state()["sweep"]["center_hz"] == 600_000_000, what="行から Center に")
        acts["row_remove"].trigger()
        self.wait(lambda: not self.markers(), what="行から消す")

    def test_table_scrolls_not_clipped(self):
        for f in range(8):
            self.cmd("marker_add", kind="M", trace=1, freq_hz=200_000_000 + f * 100_000_000)
        self.pump()
        tree = self.win.marker_panel.tree
        self.assertEqual(tree.topLevelItemCount(), 8)
        tree.scrollToItem(tree.topLevelItem(7))
        self.pump(0.1)
        r = tree.visualItemRect(tree.topLevelItem(7))
        self.assertTrue(tree.viewport().rect().contains(r), (r, tree.viewport().rect()))


class MarkerTabTest(MarkerBase):
    def setUp(self):
        super().setUp()
        self.cmd("marker_add", kind="M", trace=1, freq_hz=1_500_000_000)
        self.pump()
        self.win.select_marker("M1")
        self.tab = self.win.marker_tab

    def test_offsets_become_child_rows(self):
        self.tab.off_value.setText("10M")
        self.tab.off_sign.setCurrentIndex(self.tab.off_sign.findData("±"))
        self.tab.off_add.click()
        self.wait(lambda: len(self.marker("M1")["freq_offsets"]) == 2, what="±10MHz")
        self.tab.off_type.setCurrentIndex(1)
        self.tab.off_value.setText("3")
        self.tab.off_sign.setCurrentIndex(self.tab.off_sign.findData("-"))
        self.tab.off_add.click()
        self.wait(lambda: len(self.marker("M1")["level_offsets"]) == 1, what="−3dB")
        self.pump()
        rows = self.table_rows()
        self.assertIn("  M1 +10 MHz", rows)
        self.assertIn("  M1 −10 MHz", rows)
        self.assertIn("  M1 −3 dB", rows)

    def test_functions_and_track_reason(self):
        self.assertFalse(self.tab.track.isEnabled())
        self.assertIn("P（ピーク）", self.tab.track.toolTip())
        self.tab.bw1.setChecked(True)
        self.wait(lambda: self.marker("M1")["bandwidth"], what="3dB 帯域幅")
        self.pump()
        self.assertTrue(any("3dB 帯域幅" in r for r in self.table_rows()))

    def test_numeric_frequency_snaps_and_notes(self):
        self.tab.freq.edit.setFocus()
        self.tab.freq.edit.selectAll()
        QTest.keyClicks(self.tab.freq.edit, "1234.5678")
        QTest.keyClick(self.tab.freq.edit, Qt.Key.Key_Return)
        self.wait(lambda: self.marker("M1")["freq_hz"] == 1_235_000_000, what="近い点へ")
        # 命令の結果（知らせ）はマーカーの更新の後に届く。届くのを待つ
        self.wait(lambda: "の点に置きました" in self.tab.freq.note.text(), what="点に置いた知らせ")

    def test_xy_cursors(self):
        self.tab.xy_checks["x1_hz"].setChecked(True)
        self.tab.xy_checks["x2_hz"].setChecked(True)
        self.tab.xy_checks["y1"].setChecked(True)
        self.wait(lambda: self.state()["markers"]["xy"]["dx_hz"] is not None, what="X1・X2")
        self.pump()
        rows = self.table_rows()
        for name in ("X1", "X2", "Y1"):
            self.assertIn(name, rows)
        self.assertIn("ΔX", self.tab.dx.text())
        self.assertTrue(self.win.marker_layer.xy["x1_hz"].isVisible())
        # 線を動かし終えたらコアへ送る
        self.win._on_xy_moved("x1_hz", 321_000_000.4)
        self.wait(lambda: self.state()["markers"]["xy"]["x1_hz"] == 321_000_000, what="X1 を動かす")
        self.tab.xy_checks["x1_hz"].setChecked(False)
        self.wait(lambda: self.state()["markers"]["xy"]["x1_hz"] is None, what="X1 を外す")

    def test_peak_list_register_and_top_n(self):
        self.tab.pk_find.click()
        self.wait(lambda: self.tab.pk_table.rowCount() >= 2, what="ピークの一覧")
        self.tab.pk_table.cellWidget(0, 3).click()
        self.wait(lambda: any(m["id"] == "P1" for m in self.markers()), what="P に登録")
        self.tab.top_n.setValue(2)
        self.tab.top_btn.click()
        self.wait(lambda: {"P1", "P2"} <= {m["id"] for m in self.markers()}, what="上位 2 個に P")


if __name__ == "__main__":
    unittest.main()
