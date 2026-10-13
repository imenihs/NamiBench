"""マーカーの機能の dB の欄（打てるドロップダウン）とグラフの機能の線（設計G §3.6。Qt）。

- 「N dB 帯域幅」「2つ目の N」「しきい値との交点」「床から」: 一覧から選ぶ・打つ・欄の上のホイールで 1dB・
  範囲の外と読めない値は欄の下に知らせて戻す・Esc で打つ前に戻す・しきい値の一覧は縦軸の単位
- 機能の線: 結果が出ると出る・掃引ごとに結果と一緒に動く・切り替えで消える（コアの view.function_lines）・
  交点が無い側には縦線を出さない・つまんで動かせない
"""
import unittest

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest

from namibench.ui_qt import marker_rows
from namibench.ui_qt.fields import BANDWIDTH_CHOICES, FLOOR_CHOICES, REF_CHOICES
from tests.test_ui_qt_support import UiBase, app

MHZ = 1_000_000


def wheel(widget, up=True):
    ev = QWheelEvent(QPointF(5, 5), QPointF(widget.mapToGlobal(QPoint(5, 5))), QPoint(0, 0),
                     QPoint(0, 120 if up else -120), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                     Qt.ScrollPhase.NoScrollPhase, False)
    app.sendEvent(widget, ev)


class FunctionBase(UiBase):
    start, stop, single = 1400 * MHZ, 1600 * MHZ, True

    def setUp(self):
        super().setUp()
        self.connect_and_sweep(start=self.start, stop=self.stop, single=self.single)
        self.cmd("marker_add", kind="M", trace=1, freq_hz=1500 * MHZ)
        self.pump()
        self.win.select_marker("M1")
        self.tab = self.win.marker_tab

    def marker(self):
        return next(m for m in self.state()["markers"]["markers"] if m["id"] == "M1")

    def drawn(self):
        """グラフに描いている機能の線: [(角度, 位置)]（横線は 0 度・レベル、縦線は 90 度・周波数）。"""
        return sorted((int(ln.angle), round(float(ln.value()), 3)) for ln in self.win.marker_layer.function_items if ln.isVisible())

    def expected(self):
        """いまの画面のマーカー表の行から作った線（Qt・Web・PNG が同じ行の line を使う）。"""
        out = []
        for r in marker_rows.rows_from_snapshot(self.win.marker_snap):
            if r.get("line"):
                out.append((0, round(r["line"]["level"], 3)))
                out += [(90, float(f)) for f in r["line"]["freqs_hz"]]
        return sorted(out)

    def focus(self, field):
        """欄をクリックした状態にする（スクロールする場所の欄は、フォーカスがあるときだけホイールで変わる: J §4.10）。"""
        self.win.show_part("tab-marker")
        field.edit.setFocus()
        self.pump(0.05)
        self.assertTrue(field.edit.hasFocus())

    def type_into(self, field, text, key=Qt.Key.Key_Return):
        field.edit.setFocus()
        field.edit.selectAll()
        field.edit.insert(text)  # QTest.keyClicks は Latin-1 の外の字（−）を送れないので、打った文字として入れる
        QTest.keyClick(field.edit, key)
        self.pump(0.1)


class DbFieldsTest(FunctionBase):
    def test_lists(self):
        self.assertEqual(self.tab.bw1_n.choices(), [f"{v} dB" for v in BANDWIDTH_CHOICES])
        self.assertEqual(self.tab.bw2_n.choices(), [f"{v} dB" for v in BANDWIDTH_CHOICES])
        self.assertEqual(self.tab.pk_margin.choices(), [f"{v} dB" for v in FLOOR_CHOICES])
        # しきい値は REF レベルと同じ一覧（+20〜−120 を 10dB 刻み）を、いまの縦軸の単位で
        th = self.tab.th_level.choices()
        self.assertEqual(len(th), len(REF_CHOICES))
        self.assertEqual((th[0], th[2], th[-1]), ("+20 dBm", "0 dBm", "−120 dBm"))
        self.assertEqual(th, [self.win.f_ref.combo.itemText(i) for i in range(self.win.f_ref.combo.count())])
        self.assertEqual(self.tab.bw1_n.edit.text(), "3.0 dB")
        self.assertEqual(self.tab.th_level.edit.text(), "−20.0 dBm")

    def test_bandwidth_choose_type_wheel_and_invalid(self):
        f = self.tab.bw1_n
        self.tab.bw1.setChecked(True)
        self.wait(lambda: [b["n_db"] for b in self.marker()["bandwidth"]] == [3.0], what="3dB 帯域幅を入れる")
        # 一覧から選ぶ
        f.combo.activated.emit(f.choices().index("6 dB"))
        self.wait(lambda: [b["n_db"] for b in self.marker()["bandwidth"]] == [6.0], what="一覧から 6dB")
        self.assertEqual(f.edit.text(), "6.0 dB")
        # 打つ（小数1桁に丸める。前の欄と同じ）
        self.type_into(f, "10.04")
        self.wait(lambda: [b["n_db"] for b in self.marker()["bandwidth"]] == [10.0], what="打った 10")
        # 欄の上のホイールは、カーソルの桁によらず 1dB
        f.edit.setCursorPosition(4)
        wheel(f.edit, up=True)
        self.wait(lambda: [b["n_db"] for b in self.marker()["bandwidth"]] == [11.0], what="ホイールで +1dB")
        wheel(f.combo, up=False)
        self.wait(lambda: [b["n_db"] for b in self.marker()["bandwidth"]] == [10.0], what="矢印の上でも 1dB")
        # ↑↓ はカーソルの桁（10.0 の 1 の左 = 10 の位）
        f.edit.setCursorPosition(1)
        QTest.keyClick(f.edit, Qt.Key.Key_Up)
        self.wait(lambda: [b["n_db"] for b in self.marker()["bandwidth"]] == [20.0], what="↑ で 10 の位")
        # 範囲の外（0.1〜200 dB）は欄の下に知らせて戻す。コアへは送らない
        self.type_into(f, "500")
        self.assertEqual(f.note.text(), "0.1〜200 dB の値を入れてください")
        self.assertTrue(f._popup.isVisible())
        self.assertEqual(f.edit.text(), "20.0 dB")
        self.type_into(f, "0")
        self.assertEqual(f.edit.text(), "20.0 dB")
        # 読めない値も戻す
        self.type_into(f, "abc")
        self.assertEqual(f.note.text(), "読めない値です")
        self.assertEqual(f.edit.text(), "20.0 dB")
        self.pump(0.3)
        self.assertEqual([b["n_db"] for b in self.marker()["bandwidth"]], [20.0])
        # Esc は打つ前の値に戻す（送らない）
        f.edit.setFocus()
        f.edit.selectAll()
        f.edit.insert("40")
        QTest.keyClick(f.edit, Qt.Key.Key_Escape)
        self.assertEqual(f.edit.text(), "20.0 dB")
        # 2つ目の N を入れるとシェープファクタも出る
        self.tab.bw2.setChecked(True)
        self.wait(lambda: [b["n_db"] for b in self.marker()["bandwidth"]] == [20.0, 60.0], what="2つ目の N")

    def test_old_echo_does_not_rewrite_while_sending(self):
        """続けて回したホイールの値が、前に送った値の戻りで書き戻されない（戻りを待つ間は書き直さない）。"""
        f = self.tab.bw1_n
        self.tab.bw1.setChecked(True)
        self.wait(lambda: [b["n_db"] for b in self.marker()["bandwidth"]] == [3.0], what="3dB 帯域幅")
        self.pump()
        old = dict(self.tab._marker)  # N = 3 のときのマーカー
        self.focus(f)
        wheel(f.edit)
        wheel(f.edit)  # 5 を送った（戻りはまだ受けていない）
        self.tab.apply(old, self.tab._snap)  # 前の値（3）の戻りが届いた
        self.assertEqual((f.value(), f.edit.text()), (5.0, "5.0 dB"))
        wheel(f.edit)
        self.wait(lambda: [b["n_db"] for b in self.marker()["bandwidth"]] == [6.0], what="続けて 6dB")
        self.wait(lambda: self.tab._fn_pending == 0 and f.edit.text() == "6.0 dB", what="戻りを受けて 6dB")

    def test_wheel_stops_at_range_end(self):
        f = self.tab.bw1_n
        f.combo.activated.emit(f.choices().index("3 dB"))
        self.focus(f)
        for _ in range(5):
            wheel(f.edit, up=False)
        self.assertEqual(f.value(), 0.1)  # 下の端で止まる（0 以下にしない）
        self.assertEqual(f.edit.text(), "0.1 dB")

    def test_threshold_choose_and_type_minus(self):
        f = self.tab.th_level
        self.tab.th.setChecked(True)
        self.wait(lambda: self.marker()["threshold"] is not None, what="しきい値を入れる")
        f.combo.activated.emit(f.choices().index("−60 dBm"))
        self.wait(lambda: self.marker()["threshold"]["level"] == -60.0, what="一覧から −60dBm")
        self.type_into(f, "−47.5")
        self.wait(lambda: self.marker()["threshold"]["level"] == -47.5, what="打った −47.5")
        self.type_into(f, "-400")
        self.assertEqual(f.note.text(), "−300〜300 dBm の値を入れてください")
        self.assertEqual(f.edit.text(), "−47.5 dBm")

    def test_floor_margin_used_by_find(self):
        f = self.tab.pk_margin
        self.assertEqual(f.edit.text(), "10.0 dB")
        f.combo.activated.emit(f.choices().index("20 dB"))
        self.assertEqual((f.value(), f.edit.text()), (20.0, "20.0 dB"))
        self.type_into(f, "101")
        self.assertEqual(f.note.text(), "0〜100 dB の値を入れてください")
        self.assertEqual(f.value(), 20.0)
        sent = []
        orig = self.win.call
        self.win.call = lambda name, **kw: (sent.append((name, kw)), orig(name, **kw))[1]
        try:
            self.tab.pk_find.click()
        finally:
            self.win.call = orig
        self.assertEqual([kw["floor_margin_db"] for name, kw in sent if name == "peak_list"], [20.0])


class FunctionLinesTest(FunctionBase):
    single = False

    def test_lines_follow_results_and_toggle(self):
        self.assertEqual(self.drawn(), [])  # 機能を入れるまでは無い
        self.tab.bw1.setChecked(True)
        self.wait(lambda: self.drawn() and self.drawn() == self.expected(), what="3dB 帯域幅の線")
        m = next(x for x in self.win.marker_snap["markers"] if x["id"] == "M1")
        b = m["bandwidth"][0]
        self.assertEqual(self.drawn(), sorted([(0, round(m["level"] - 3.0, 3)), (90, float(b["left_hz"])),
                                               (90, float(b["right_hz"]))]))
        # つまんで動かせない・XY カーソルとは別（読み値の表に出ない）
        self.assertTrue(all(not ln.movable for ln in self.win.marker_layer.function_items if ln.isVisible()))
        self.assertFalse(any(r["kind"] in ("freq_cursor", "level_cursor") for r in self.win.marker_row_list))
        # 掃引ごとに結果と一緒に動く（シミュレータの雑音で交点が毎回少し動く）
        first = self.drawn()
        self.wait(lambda: self.drawn() != first, timeout=15, what="次の掃引で線が動く")
        self.assertEqual(self.drawn(), self.expected())
        # しきい値との交点: 横線 = しきい値、縦線 = 交点
        self.tab.th.setChecked(True)
        self.wait(lambda: any(r.get("line") and r["id"] == "M1.th" for r in marker_rows.rows_from_snapshot(
            self.win.marker_snap)), what="しきい値の線")
        self.assertIn((0, -20.0), self.drawn())
        # 切り替えで消す（コアの表示の設定。掃引していなくても消える）
        self.cmd("stop")
        self.tab.function_lines.setChecked(False)
        self.wait(lambda: self.state()["view"]["function_lines"] is False, what="コアへ送る")
        self.wait(lambda: self.drawn() == [], what="線が消える")
        # 別の口（Web など）から戻しても画面が合う
        self.cmd("set_view", function_lines=True)
        self.wait(lambda: self.tab.function_lines.isChecked() and self.drawn() == self.expected() and self.drawn(),
                  what="線が戻る")


class OneSideTest(FunctionBase):
    # 信号（1500MHz）を表示範囲の右端に置く: 右側は端まで交点が無い
    start, stop = 1400 * MHZ, 1500 * MHZ

    def test_no_vertical_line_on_side_without_crossing(self):
        self.tab.bw1.setChecked(True)
        self.wait(lambda: self.marker()["bandwidth"], what="3dB 帯域幅")
        self.pump()
        b = self.marker()["bandwidth"][0]
        self.assertEqual((b["left_status"], b["right_status"]), ("ok", "edge"))
        self.assertEqual([a for a, _v in self.drawn()], [0, 90])  # 横線と左の縦線だけ
        self.assertIn((90, float(b["left_hz"])), self.drawn())
        # どちらにも交点が無い（しきい値 −200dBm 以上は全部）: 横線だけ
        self.tab.bw1.setChecked(False)
        self.tab.th_side.setCurrentIndex(self.tab.th_side.findData("above"))
        self.tab.th_level.combo.activated.emit(self.tab.th_level.choices().index("−120 dBm"))
        self.type_into(self.tab.th_level, "-200")
        self.tab.th.setChecked(True)
        self.wait(lambda: (self.marker().get("threshold") or {}).get("level") == -200.0, what="しきい値 −200")
        self.pump()
        self.assertEqual(self.drawn(), [(0, -200.0)])


if __name__ == "__main__":
    unittest.main()
