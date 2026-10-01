"""状態を欄へ映すときに、利用者が触っている欄を書き直さないこと（実装リスト 3-9）と、
ドロップダウンが一番長い選択肢の切れない幅を取ること（実装リスト 3-8・設計J §4.3）を確かめる。
"""
import unittest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QComboBox

from tests.test_ui_qt_rtlsdr import MHZ, RtlUiBase
from tests.test_ui_qt_support import UiBase


class FieldSyncTest(UiBase):
    def sweeps(self, n=3):
        """連続掃引で n 回の掃引（状態の知らせ）を流す。"""
        n0 = self.state()["run"]["sweep_count"]
        self.wait(lambda: self.state()["run"]["sweep_count"] >= n0 + n, timeout=15, what=f"{n} 回の掃引")
        self.pump(0.2)

    def focus(self, w):
        self.win.activateWindow()
        w.setFocus(Qt.FocusReason.MouseFocusReason)
        self.pump(0.05)
        self.assertTrue(w.hasFocus())

    def leave(self):
        """フォーカスを外す（グラフへ移す）。"""
        self.win.plot.setFocus(Qt.FocusReason.MouseFocusReason)
        self.pump(0.2)

    def assert_kept(self, edit, sel, pos):
        self.assertEqual(edit.selectedText(), sel)
        self.assertEqual(edit.cursorPosition(), pos)

    def test_trace_count_spin_keeps_selection(self):
        self.connect_and_sweep(single=False)
        self.cmd("set_trace", number=1, action="average", count=16)
        self.pump()
        spin = self.win.trace_tab.rows[1].count
        self.focus(spin)
        le = spin.lineEdit()
        le.setSelection(0, 1)
        sel, pos = le.selectedText(), le.cursorPosition()
        self.assertEqual(sel, "1")
        # ほかの口で回数を変えても、触っている間は書き直さない
        self.cmd("set_trace", number=1, count=32)
        self.sweeps()
        self.assert_kept(le, sel, pos)
        self.assertEqual(le.text(), "16 回")
        self.leave()
        self.assertEqual(le.text(), "32 回")

    def test_ref_field_keeps_selection_and_follows_after_focus_out(self):
        self.connect_and_sweep(single=False)
        e = self.win.f_ref.edit
        self.focus(e)
        e.setSelection(1, 2)
        sel, pos = e.selectedText(), e.cursorPosition()
        self.cmd("set_amplitude", ref_level=-37.0)
        self.sweeps()
        self.assert_kept(e, sel, pos)
        self.assertEqual(e.text(), "0.0 dBm")
        self.leave()
        self.assertEqual(e.text(), "−37.0 dBm")

    def test_center_field_keeps_selection_and_shows_own_commit(self):
        self.connect_and_sweep(single=False)
        e = self.win.f_center.edit
        self.focus(e)
        e.setSelection(2, 3)
        sel, pos = e.selectedText(), e.cursorPosition()
        self.sweeps()
        self.assert_kept(e, sel, pos)
        # 打って Enter で確定した値は、フォーカスがあってもその場で正規の形になる
        e.selectAll()
        QTest.keyClicks(e, "600M")
        QTest.keyClick(e, Qt.Key.Key_Return)
        self.wait(lambda: e.text() == "600.000 MHz", what="確定した値が欄に出る")
        self.assertTrue(e.hasFocus())
        # ↑ で桁を送った値もその場で出る
        e.setCursorPosition(1)
        QTest.keyClick(e, Qt.Key.Key_Up)
        self.wait(lambda: e.text() == "700.000 MHz", what="↑ で送った値が欄に出る")
        # 範囲を選んだ後は（Shift+→ で選ぶ）、別の口の変更で書き直さない
        e.setCursorPosition(0)
        for _ in range(3):
            QTest.keyClick(e, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
        self.cmd("set_sweep", center_hz=800 * MHZ)
        self.sweeps(2)
        self.assertEqual(e.selectedText(), "700")
        self.leave()
        self.assertEqual(e.text(), "800.000 MHz")

    def test_marker_freq_field_keeps_selection(self):
        self.connect_and_sweep(single=False)
        r = self.cmd("marker_add", kind="M", trace=1, freq_hz=500_000_000)
        self.win.select_marker(r["id"])
        self.pump()
        e = self.win.marker_tab.freq.edit
        self.focus(e)
        e.setSelection(0, 3)
        sel, pos = e.selectedText(), e.cursorPosition()
        self.cmd("marker_set", id=r["id"], freq_hz=600_000_000)
        self.sweeps()
        self.assert_kept(e, sel, pos)
        self.leave()
        self.assertTrue(e.text().startswith("600.0"), e.text())

    def test_open_popup_is_not_rewritten(self):
        self.connect_and_sweep(single=False)
        combo = self.win.rbw_combo
        before = combo.currentIndex()
        combo.showPopup()
        self.pump(0.1)
        self.assertTrue(combo.view().isVisible())
        other = next(combo.itemData(i) for i in range(combo.count())
                     if combo.itemData(i) is not None and i != before)
        self.cmd("set_sweep", rbw_hz=other)
        self.sweeps()
        self.assertEqual(combo.currentIndex(), before)
        combo.hidePopup()
        self.pump(0.3)
        self.assertEqual(combo.currentData(), other)


def widest_text(combo: QComboBox) -> int:
    fm = combo.view().fontMetrics()
    return max(fm.horizontalAdvance(combo.itemText(i)) for i in range(combo.count()))


class ComboWidthTest(RtlUiBase):
    def test_every_combo_fits_its_longest_item(self):
        """RTL-SDR の RBW（窓の見出しと長い選択肢）を含め、欄も開いた一覧も一番長い選択肢が切れない。"""
        self.connect_rtl()
        self.pump(0.3)
        combo = self.win.rbw_combo
        self.assertTrue(any(combo.itemData(i) is None for i in range(combo.count())), "窓の見出しがある")
        combos = [c for c in self.win.findChildren(QComboBox) if c.count() and c.isVisible()]
        self.assertGreaterEqual(len(combos), 5)
        for c in combos:
            self.assertEqual(c.sizeAdjustPolicy(), QComboBox.SizeAdjustPolicy.AdjustToContents, c.objectName())
            self.assertGreaterEqual(c.view().minimumWidth(), widest_text(c))
            # 欄に出る文字も切れない（一番長い選択肢に、枠と一覧の矢印の分を足した幅がある）
            self.assertGreaterEqual(c.width(), widest_text(c) + 16, c.currentText())
        combo.showPopup()
        self.pump(0.1)
        self.assertGreaterEqual(combo.view().width(), widest_text(combo))
        combo.hidePopup()

    def test_top_bar_boxes_have_one_size(self):
        self.connect_rtl()
        w = self.win
        boxes = (w.rbw_combo, w.f_ref.combo, w.div_combo)
        self.assertEqual(len({c.height() for c in boxes}), 1, [c.height() for c in boxes])
        self.assertEqual(len({c.font().pointSize() for c in boxes}), 1)


if __name__ == "__main__":
    unittest.main()
