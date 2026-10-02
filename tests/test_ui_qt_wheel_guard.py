"""スクロールする場所の欄は、フォーカスがあるときだけホイールで変わる（設計J §4.10 の 1。Qt）。

- 右のパネルのタブの一覧（表示タブの単位）: フォーカスが無ければ値を変えず、ホイールはタブのスクロールに回る。
  ホイールでフォーカスを取らない。クリックしてフォーカスがあれば変わる
- 右のパネルの数値の欄（外部補正）・打てるドロップダウン（トレースの回数）・マーカー表の一覧（基準）も、
  フォーカスが無ければ変えない
- 上段（REF・RBW・dB/div）はスクロールしない場所なので、フォーカスが無くてもポインタの下のホイールで変わる
- 開いた一覧はホイールでスクロールする。切り離した窓（ピークの一覧）はスクロールしない場所に戻る
"""
import unittest

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QScrollArea

from tests.test_ui_qt_support import UiBase, app

MHZ = 1_000_000


def wheel(widget, up=True):
    ev = QWheelEvent(QPointF(5, 5), QPointF(widget.mapToGlobal(QPoint(5, 5))), QPoint(0, 0),
                     QPoint(0, 120 if up else -120), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                     Qt.ScrollPhase.NoScrollPhase, False)
    app.sendEvent(widget, ev)


def enclosing_scroll_area(w) -> QScrollArea:
    p = w.parentWidget()
    while p is not None and not isinstance(p, QScrollArea):
        p = p.parentWidget()
    return p


class PanelWheelTest(UiBase):
    size = (1600, 520)  # 表示タブがタブの中でスクロールする高さ

    def setUp(self):
        super().setUp()
        self.win.show_part("tab-view")
        self.pump(0.1)

    def unit(self):
        return self.state()["view"]["level_unit"]

    def test_unfocused_list_scrolls_panel_and_keeps_value(self):
        combo = self.win.display_tab.unit
        bar = enclosing_scroll_area(combo).verticalScrollBar()
        self.assertGreater(bar.maximum(), 0, "前提: 表示タブがスクロールできる高さ")
        bar.setValue(0)
        before = self.unit()
        self.assertFalse(combo.hasFocus())
        wheel(combo, up=False)
        self.pump(0.2)
        self.assertEqual(self.unit(), before)
        self.assertEqual(combo.currentData(), before)
        self.assertGreater(bar.value(), 0, "ホイールはタブのスクロールに回る")
        self.assertFalse(combo.hasFocus())
        # ホイールでフォーカスを取らない（Qt はホイールを届ける前に WheelFocus の欄へフォーカスを渡すため、外してある）
        self.assertEqual(combo.focusPolicy(), Qt.FocusPolicy.StrongFocus)

    def test_focused_list_changes_with_wheel(self):
        combo = self.win.display_tab.unit
        bar = enclosing_scroll_area(combo).verticalScrollBar()
        before = self.unit()
        combo.setFocus(Qt.FocusReason.MouseFocusReason)
        self.pump(0.05)
        self.assertTrue(combo.hasFocus())
        y = bar.value()
        wheel(combo, up=False)
        self.wait(lambda: self.unit() != before, what="フォーカスがあればホイールで単位が変わる")
        self.assertEqual(bar.value(), y)  # このときはスクロールしない

    def test_unfocused_number_fields_keep_value(self):
        # 外部補正（数値の欄）
        self.cmd("set_view", ext_db=10.0)
        ext = self.win.display_tab.ext
        self.wait(lambda: ext.edit.text().startswith("10"), what="外部補正 10dB を映す")
        wheel(ext.edit, up=True)
        self.pump(0.2)
        self.assertEqual(self.state()["view"]["ext_db"], 10.0)
        # トレースの回数（打てるドロップダウン。行の上・矢印の上）
        self.cmd("set_trace", number=1, action="max_hold", count=8)
        count = self.win.trace_tab.rows[1].count
        self.wait(lambda: count.value() == 8 and count.isEnabled(), what="回数 8 を映す")
        wheel(count.lineEdit(), up=True)
        wheel(count, up=True)
        self.pump(0.2)
        self.assertEqual(self.state()["traces"][0]["count"], 8)
        # フォーカスがあれば変わる（数値の欄の決まりは今まで通り）
        count.lineEdit().setFocus()
        self.pump(0.05)
        wheel(count.lineEdit(), up=True)
        self.wait(lambda: self.state()["traces"][0]["count"] == 9, what="フォーカスがあれば回数 +1")

    def test_top_bar_changes_without_focus(self):
        f = self.win.f_ref
        self.assertFalse(f.combo.hasFocus())
        ref0 = -40.0
        self.cmd("set_amplitude", ref_level=ref0)
        self.wait(lambda: f.value() == ref0, what="REF −40dBm を映す")
        wheel(f.edit, up=True)
        self.wait(lambda: self.state()["amplitude"]["ref_level"] == ref0 + 1, what="REF の欄の上で +1dB")
        wheel(f.combo, up=True)
        self.wait(lambda: self.state()["amplitude"]["ref_level"] == ref0 + 2, what="REF の矢印の上で +1dB")
        rbw = self.win.rbw_combo
        self.assertFalse(rbw.hasFocus())
        self.assertEqual(rbw.focusPolicy(), Qt.FocusPolicy.WheelFocus)  # 上段は今まで通り
        self.cmd("set_sweep", start_hz=100 * MHZ, stop_hz=110 * MHZ)  # 狭い Span: どの RBW も選べる
        self.wait(lambda: self.state()["sweep"]["rbw_hz"] == rbw.itemData(0), what="RBW は一覧の先頭")
        wheel(rbw, up=False)
        self.wait(lambda: self.state()["sweep"]["rbw_hz"] == rbw.itemData(1), what="RBW の一覧の上で次の RBW")
        div = self.win.div_combo
        d0 = div.currentIndex()
        wheel(div, up=d0 >= div.count() - 1)
        self.wait(lambda: div.currentIndex() != d0, what="dB/div の一覧の上で変わる")

    def test_open_list_scrolls(self):
        self.win.show_part("tab-trace")
        self.pump(0.1)
        count = self.win.trace_tab.rows[1].count
        self.cmd("set_trace", number=1, action="max_hold", count=8)
        self.wait(lambda: count.isEnabled(), what="回数を選べる")
        self.assertGreater(count.count(), count.maxVisibleItems(), "前提: 一覧がスクロールする長さ")
        count.showPopup()
        self.pump(0.1)
        view = count.view()
        bar = view.verticalScrollBar()
        bar.setValue(0)
        wheel(view.viewport(), up=False)
        self.pump(0.1)
        self.assertGreater(bar.value(), 0)
        count.hidePopup()
        self.pump(0.1)
        self.assertEqual(self.state()["traces"][0]["count"], 8)

    def test_detached_peaks_is_not_a_scroll_place(self):
        spin = self.win.marker_tab.top_n
        self.win.show_part("tab-marker")
        self.pump(0.1)
        v0 = spin.value()
        wheel(spin, up=True)
        self.assertEqual(spin.value(), v0)
        self.assertEqual(spin.focusPolicy(), Qt.FocusPolicy.StrongFocus)
        # 切り離した窓はスクロールしない場所: 今まで通りフォーカスが無くても変わる
        self.win.parts.detach("peaks")
        self.pump(0.2)
        self.assertIsNone(enclosing_scroll_area(spin))
        self.assertEqual(spin.focusPolicy(), Qt.FocusPolicy.WheelFocus)
        self.assertFalse(spin.hasFocus())
        wheel(spin, up=True)
        self.assertEqual(spin.value(), v0 + 1)
        self.win.parts.peaks.attach()
        self.pump(0.2)


class MarkerTableWheelTest(UiBase):
    def test_unfocused_ref_list_keeps_value(self):
        self.connect_and_sweep(start=1400 * MHZ, stop=1600 * MHZ)
        self.cmd("marker_add", kind="M", trace=1, freq_hz=1450 * MHZ)
        self.cmd("marker_add", kind="M", trace=1, freq_hz=1550 * MHZ)
        table = self.win.marker_panel
        self.wait(lambda: len(table._ref_combos) == 2, what="2つのマーカーの行")
        cb = table._ref_combos["M1"]
        self.wait(lambda: cb.count() == 2, what="基準の一覧（なし・M2）")
        self.assertEqual(cb.currentIndex(), 0)
        wheel(cb, up=False)
        self.pump(0.3)
        m1 = next(m for m in self.state()["markers"]["markers"] if m["id"] == "M1")
        self.assertFalse(m1.get("ref"))
        self.assertEqual(cb.currentIndex(), 0)


if __name__ == "__main__":
    unittest.main()


class SliderWheelTest(UiBase):
    def test_unfocused_volume_slider_keeps_value(self):
        """マーカーのタブの音量のスライダーも、フォーカスが無ければホイールで変えない（スクロールバーは今まで通り）。"""
        self.win.show_part("tab-marker")
        self.pump(0.1)
        s = self.win.listen.volume
        before = s.value()
        self.win.plot.setFocus()
        self.pump(0.05)
        wheel(s, up=True)
        self.pump(0.05)
        self.assertEqual(s.value(), before)
        s.setFocus()
        self.pump(0.05)
        wheel(s, up=True)
        self.pump(0.05)
        self.assertNotEqual(s.value(), before)
