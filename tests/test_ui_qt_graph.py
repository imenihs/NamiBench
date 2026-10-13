"""グラフの上の操作・Span 固定と反対の規則のボタン・メニューとキーを確かめる（設計J §3・§4.1・§4.3.1・§4.6、C §5.11）。

シミュレータの本体を使い、画面の操作から本体の採用値までを通す。
"""
import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent, QWheelEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from namibench.api.local import LocalApi, _SimulatorBackend  # noqa: E402
from namibench.core.devices.base import load_profile  # noqa: E402
from namibench.core.instrument import GIGAST_PROFILE_PATH, Instrument  # noqa: E402
from namibench.ui_qt import texts  # noqa: E402
from namibench.ui_qt.main_window import MainWindow  # noqa: E402

app = QApplication.instance() or QApplication([])


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        profile = load_profile(GIGAST_PROFILE_PATH)
        sim = _SimulatorBackend(profile)
        sim.speed = 0.05
        self.api = LocalApi(Instrument({"simulator": sim}, profile=profile,
                                       data_root=root / "d", cal_root=root / "c"))
        self.win = MainWindow(self.api)
        self.win.resize(1600, 900)
        self.win.show()
        self.pump()
        # 起動時に繋がっていないので開く機器の一覧（J §4.7）を閉じ、主の窓でキーを試す
        end = time.monotonic() + 8
        while self.win.device_window is None and time.monotonic() < end:
            self.pump(0.05)
        self.win.device_window.close()
        self.pump(0.05)

    def tearDown(self):
        self.win.close()
        self.api.close()
        self.tmp.cleanup()

    def pump(self, seconds=0.3):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)

    def sweep(self):
        return self.api.get_state().result(5)["sweep"]

    def set_sweep(self, **kw):
        r = self.api.call("set_sweep", **kw).result(5)
        self.pump()
        return r

    def type_into(self, field, text):
        e = field.edit
        e.setFocus()
        e.selectAll()
        QTest.keyClicks(e, text)
        QTest.keyClick(e, Qt.Key.Key_Return)
        self.pump()

    # ---- グラフの座標
    @property
    def vb(self):
        return self.win.plot.getPlotItem().getViewBox()

    def view_to_widget(self, x, y) -> QPoint:
        return self.win.plot.mapFromScene(self.vb.mapViewToScene(QPointF(x, y)))

    def axis_point(self, name) -> QPoint:
        """軸の数字のあたり（軸の部品は目盛線の分だけグラフの中へ広がっているので、グラフの外側を指す）。"""
        r = self.vb.sceneBoundingRect()
        p = QPointF(r.left() - 15, r.center().y()) if name == "left" else QPointF(r.center().x(), r.bottom() + 12)
        return self.win.plot.mapFromScene(p)

    def mouse(self, kind, pos: QPoint, button, buttons, mods=Qt.KeyboardModifier.NoModifier):
        vp = self.win.plot.viewport()
        ev = QMouseEvent(kind, QPointF(pos), QPointF(vp.mapToGlobal(pos)), button, buttons, mods)
        QApplication.sendEvent(vp, ev)

    def drag(self, p0: QPoint, p1: QPoint, shift=False):
        L = Qt.MouseButton.LeftButton
        mods = Qt.KeyboardModifier.ShiftModifier if shift else Qt.KeyboardModifier.NoModifier
        self.mouse(QEvent.Type.MouseButtonPress, p0, L, L, mods)
        steps = 8
        for i in range(1, steps + 1):
            p = p0 + (p1 - p0) * (i / steps)
            self.mouse(QEvent.Type.MouseMove, p, Qt.MouseButton.NoButton, L, mods)
        self.mouse(QEvent.Type.MouseButtonRelease, p1, L, Qt.MouseButton.NoButton, mods)
        self.pump()

    def click(self, pos: QPoint, double=False):
        L = Qt.MouseButton.LeftButton
        self.mouse(QEvent.Type.MouseButtonPress, pos, L, L)
        self.mouse(QEvent.Type.MouseButtonRelease, pos, L, Qt.MouseButton.NoButton)
        if double:
            self.mouse(QEvent.Type.MouseButtonDblClick, pos, L, L)
            self.mouse(QEvent.Type.MouseButtonRelease, pos, L, Qt.MouseButton.NoButton)
        self.pump()

    def wheel(self, pos: QPoint, clicks=1, ctrl=False):
        vp = self.win.plot.viewport()
        mods = Qt.KeyboardModifier.ControlModifier if ctrl else Qt.KeyboardModifier.NoModifier
        ev = QWheelEvent(QPointF(pos), QPointF(vp.mapToGlobal(pos)), QPoint(0, 0), QPoint(0, 120 * clicks),
                         Qt.MouseButton.NoButton, mods, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(vp, ev)
        self.pump()


class GraphOperationTest(_Base):
    def test_plain_drag_and_click_change_nothing(self):
        """Shift なしのドラッグ・1回のクリックでは範囲もマーカーも変えない（誤操作を防ぐ）。"""
        self.set_sweep(start_hz=0, stop_hz=2_000_000_000)
        p0 = self.view_to_widget(500e6, -50)
        self.drag(p0, self.view_to_widget(800e6, -50))
        self.click(p0)
        self.pump(0.3)
        sw = self.sweep()
        self.assertEqual((sw["start_hz"], sw["stop_hz"]), (0, 2_000_000_000))
        self.assertEqual(self.api.get_state().result(5)["markers"]["markers"], [])

    def test_double_click_places_marker(self):
        self.set_sweep(start_hz=0, stop_hz=2_000_000_000)
        self.click(self.view_to_widget(500e6, -50), double=True)
        self.pump(0.5)
        self.assertEqual(len(self.api.get_state().result(5)["markers"]["markers"]), 1)

    def test_drag_sets_start_stop(self):
        self.set_sweep(start_hz=0, stop_hz=2_000_000_000)
        p0 = self.view_to_widget(500e6, -50)
        p1 = self.view_to_widget(800e6, -50)
        self.drag(p0, p1, shift=True)
        sw = self.sweep()
        # 画素の細かさ（約 2GHz / 幅）の数画素ぶんの誤差を許す
        tol = 2e9 / self.vb.width() * 3
        self.assertAlmostEqual(sw["start_hz"], 500e6, delta=tol)
        self.assertAlmostEqual(sw["stop_hz"], 800e6, delta=tol)
        self.assertEqual(self.win.f_start.edit.text(), self.win.f_start._to_text(sw["start_hz"]))

    def test_short_drag_is_ignored(self):
        """数画素のドラッグは範囲の選択にしない（クリックのぶれ）。クリックならマーカーを置くだけ（G §3.2）。"""
        self.set_sweep(start_hz=0, stop_hz=2_000_000_000)
        p0 = self.view_to_widget(500e6, -50)
        self.drag(p0, p0 + QPoint(2, 0), shift=True)
        sw = self.sweep()
        self.assertEqual((sw["start_hz"], sw["stop_hz"]), (0, 2_000_000_000))

    def test_freq_axis_wheel_zooms_around_cursor(self):
        self.set_sweep(start_hz=0, stop_hz=2_000_000_000)
        x = self.view_to_widget(500e6, -50).x()
        pos = QPoint(x, self.axis_point("bottom").y())
        self.wheel(pos, clicks=1)
        sw = self.sweep()
        # 1段で Span が 0.8 倍。カーソルの周波数（約 500MHz）の、範囲の中の割合は変わらない
        self.assertAlmostEqual(sw["span_hz"], 1_600_000_000, delta=1e6)
        f = self.vb.mapSceneToView(self.win.plot.mapToScene(pos)).x()
        frac0 = f / 2e9
        frac1 = (f - sw["start_hz"]) / sw["span_hz"]
        self.assertAlmostEqual(frac0, frac1, delta=0.01)

    def test_level_axis_wheel_moves_ref_one_division(self):
        sw0 = self.sweep()
        self.win._set_ref(-20.0)
        self.wheel(self.axis_point("left"), clicks=1)
        self.assertAlmostEqual(self.win._ref_dbm, -10.0)
        self.wheel(self.axis_point("left"), clicks=-2)
        self.assertAlmostEqual(self.win._ref_dbm, -30.0)
        top = self.vb.viewRange()[1][1]
        self.assertAlmostEqual(top, -30.0, delta=0.1)
        # 縦軸は表示だけの値。掃引条件は変わらず、コアの振幅が正本（J §4.1）
        self.assertEqual(self.sweep(), sw0)
        self.assertEqual(self.api.get_state().result(5)["amplitude"]["ref_level"], -30.0)

    def test_ctrl_wheel_changes_db_per_div_and_keeps_ref(self):
        self.win._set_ref(-10.0)
        self.wheel(self.axis_point("left"), clicks=-1, ctrl=True)
        self.assertEqual(self.win._db_div, 5.0)
        self.assertEqual(self.win.div_combo.currentData(), 5.0)
        self.assertAlmostEqual(self.win._ref_dbm, -10.0)
        bottom = self.vb.viewRange()[1][0]
        self.assertAlmostEqual(bottom, -60.0, delta=0.1)

    def test_ctrl_wheel_reaches_20_and_40_db_per_div(self):
        # 10 → 20 → 40 で止まる（2026-10-11 ユーザー決定で 20・40 を足した）。欄の一覧も同じ並び
        combo = self.win.div_combo
        self.assertEqual([combo.itemText(i) for i in range(combo.count())],
                         ["1 dB", "2 dB", "5 dB", "10 dB", "20 dB", "40 dB"])
        self.win._set_ref(0.0)
        for want in (20.0, 40.0, 40.0):
            self.wheel(self.axis_point("left"), clicks=1, ctrl=True)
            self.assertEqual(self.win._db_div, want)
        self.assertEqual(combo.currentData(), 40.0)
        self.assertAlmostEqual(self.vb.viewRange()[1][0], -400.0, delta=0.5)
        self.assertEqual(self.win.f_ref.value(), 0.0)  # REF は動かない（J §4.1）
        ticks = self.win.plot.getPlotItem().getAxis("left")._tickLevels[0]
        self.assertEqual([t[1] for t in ticks][:3], ["0.0", "−40.0", "−80.0"])
        self.pump()
        self.assertEqual(self.api.get_state().result(5)["amplitude"]["db_per_div"], 40)  # コアへ届く
        # 欄から 20 を選ぶ
        i = combo.findData(20.0)
        combo.setCurrentIndex(i)
        combo.activated.emit(i)
        self.pump()
        self.assertEqual(self.api.get_state().result(5)["amplitude"]["db_per_div"], 20)
        self.assertAlmostEqual(self.vb.viewRange()[1][0], -200.0, delta=0.5)

    def test_level_axis_drag_moves_ref(self):
        self.win._set_ref(0.0)
        p0 = self.axis_point("left")
        h = self.vb.height()
        self.drag(p0, p0 + QPoint(0, int(h / 10)))  # 1目盛ぶん下へ
        self.assertAlmostEqual(self.win._ref_dbm, 10.0, delta=0.6)

    # 「自動」が最大値を上から1目盛下に置くこと（局発の漏れを除く・複数のトレース）は test_ui_qt_marks.py
    # （コアが出すトレースごとの最大値を使うので、本体の掃引で確かめる）

    def test_ref_auto_disabled_without_trace(self):
        self.assertFalse(self.win.btn_ref_auto.isEnabled())
        self.assertIn("波形がまだありません", self.win.btn_ref_auto.toolTip())

    def test_context_menu_items(self):
        self.set_sweep(start_hz=0, stop_hz=2_000_000_000)
        m = self.win._context_menu(700e6, -42.0)
        acts = {a.objectName(): a for a in m.actions()}
        self.assertIn("ctx_center", acts)
        self.assertIn("ctx_ref", acts)
        # 「ここにマーカー」はいつも出す（ゼロスパンでは無効にして理由）
        self.assertIn("ctx_marker", acts)
        self.assertTrue(acts["ctx_marker"].isEnabled())
        acts["ctx_center"].trigger()
        self.pump()
        self.assertEqual(self.sweep()["center_hz"], 700_000_000)
        acts["ctx_ref"].trigger()
        self.assertAlmostEqual(self.win._ref_dbm, -42.0)


class SpanLockTest(_Base):
    def test_alternative_button_keep_span(self):
        """固定 OFF: Center を守って Span を縮め、Span の欄に反対の規則のボタンを出す。押すとその1回だけ置き換える。"""
        self.set_sweep(span_hz=1_000_000_000)
        self.type_into(self.win.f_center, "11900")
        sw = self.sweep()
        self.assertEqual((sw["center_hz"], sw["span_hz"]), (11_900_000_000, 400_000_000))
        note, btn = self.win.f_span.note, self.win.f_span.note_action
        self.assertIn("400 MHz に合わせました", note.text())
        self.assertTrue(btn.isVisible())
        self.assertEqual(btn.text(), "Span 1000 MHz を保って Center を 11.6 GHz にする")
        btn.click()
        self.pump()
        sw = self.sweep()
        self.assertEqual((sw["start_hz"], sw["stop_hz"]), (11_100_000_000, 12_100_000_000))
        self.assertIsNone(sw["span_lock_hz"])
        self.assertIn("置き換えました", self.win.f_span.note.text())
        self.assertFalse(self.win.f_span.note_action.isVisible())

    def test_button_disappears_with_note(self):
        self.set_sweep(span_hz=1_000_000_000)
        self.type_into(self.win.f_center, "11900")
        self.assertTrue(self.win.f_span.note_action.isVisible())
        self.win.f_span._note_timeout()
        self.assertFalse(self.win.f_span.note_action.isVisible())
        self.assertEqual(self.win.f_span.note.text(), "")

    def test_span_lock_toggle_and_alternative_keep_center(self):
        """固定 ON: 欄に鍵と「固定」。Center を触ると Span を守り、Center の欄に反対（Center を守る）のボタン。"""
        self.set_sweep(span_hz=1_000_000_000)
        self.win.span_lock.click()
        self.pump()
        sw = self.sweep()
        self.assertEqual(sw["span_lock_hz"], 1_000_000_000)
        self.assertTrue(self.win.span_lock.isChecked())
        self.assertEqual(self.win.span_lock.text(), "固定")
        self.type_into(self.win.f_center, "11900")
        sw = self.sweep()
        self.assertEqual((sw["start_hz"], sw["stop_hz"]), (11_100_000_000, 12_100_000_000))
        btn = self.win.f_center.note_action
        self.assertTrue(btn.isVisible())
        self.assertEqual(btn.text(), "Center 11.9 GHz を保って Span を 400 MHz にする")
        btn.click()
        self.pump()
        sw = self.sweep()
        self.assertEqual((sw["center_hz"], sw["span_hz"]), (11_900_000_000, 400_000_000))
        # その1回だけの命令は固定 Span を変えない。いまの Span と違うので固定 Span も並べて出す
        self.assertEqual(sw["span_lock_hz"], 1_000_000_000)
        self.assertEqual(self.win.span_lock.text(), "固定 1000 MHz")
        # OFF に戻す
        self.win.span_lock.click()
        self.pump()
        self.assertIsNone(self.sweep()["span_lock_hz"])
        self.assertEqual(self.win.span_lock.text(), "Span 固定")

    def lock(self, **kw):
        self.set_sweep(**kw)
        self.win.span_lock.click()
        self.pump()
        self.assertIsNotNone(self.sweep()["span_lock_hz"])

    def test_wheel_with_lock_sends_nothing_and_notes(self):
        """固定 ON: 周波数軸のホイールは命令を送らず、Span の欄に1回知らせる（C §5.12）。"""
        self.lock(start_hz=0, stop_hz=2_000_000_000)
        sw0 = self.sweep()
        x = self.view_to_widget(500e6, -50).x()
        self.wheel(QPoint(x, self.axis_point("bottom").y()), clicks=1)
        self.pump()
        self.assertEqual(self.sweep(), sw0)
        self.assertEqual(self.win.f_span.note.text(), texts.LABELS["span_lock_wheel"])
        self.assertEqual(texts.LABELS["span_lock_wheel"], "Span 固定中です（幅は Span の欄で変えます）")

    def test_drag_with_lock_keeps_span(self):
        """固定 ON: ドラッグは選んだ範囲の真ん中を Center にし、幅は固定 Span のまま（C §5.12）。"""
        self.lock(start_hz=0, stop_hz=100_000_000)
        self.set_sweep(start_hz=0, stop_hz=2_000_000_000, one_shot=True)  # 表示を広げる（固定 Span は 100MHz のまま）
        self.drag(self.view_to_widget(500e6, -50), self.view_to_widget(800e6, -50), shift=True)
        sw = self.sweep()
        self.assertEqual(sw["span_hz"], 100_000_000)
        tol = 2e9 / self.vb.width() * 3
        self.assertAlmostEqual(sw["center_hz"], 650e6, delta=tol)

    def test_marker_span_releases_lock_and_notes(self):
        """固定 ON でマーカーの「デルタの2点を Start・Stop に」: 固定を外して2点を Start・Stop にし、Span の欄に1回
        知らせる（C §5.13）。"""
        self.api.call("connect", backend="simulator", serial="SIM00001").result(10)
        self.set_sweep(start_hz=100_000_000, stop_hz=1_100_000_000)
        self.api.call("run", mode="single").result(5)
        end = time.monotonic() + 10
        while time.monotonic() < end and not any(t.get("has_values") for t in self.api.get_state().result(5)["traces"]):
            self.pump(0.05)
        for f in (300_000_000, 700_000_000):
            self.api.call("marker_add", kind="M", trace=1, freq_hz=f).result(5)
        self.api.call("marker_set", id="M2", ref="M1").result(5)
        self.win.span_lock.click()
        self.pump()
        self.assertEqual(self.sweep()["span_lock_hz"], 1_000_000_000)
        marks = {m["id"]: m["freq_hz"] for m in self.api.get_state().result(5)["markers"]["markers"]}
        self.win.marker_to("M2", "span")
        self.pump()
        sw = self.sweep()
        self.assertEqual((sw["start_hz"], sw["stop_hz"]), tuple(sorted((marks["M1"], marks["M2"]))))
        self.assertIsNone(sw["span_lock_hz"])
        self.assertEqual(self.win.span_lock.text(), "Span 固定")
        self.assertEqual(self.win.f_span.note.text(), "Span 固定を外しました")
        self.assertFalse(self.win.f_span.note_action.isVisible())

    def test_start_with_lock_and_keep_typed_button(self):
        """固定 ON で Start を打つ: Stop が動いて Span を守る。上限を超えたら Start の欄に反対の結果のボタン。"""
        self.lock(start_hz=1_000_000_000, stop_hz=1_044_571_000)
        self.type_into(self.win.f_start, "470")
        sw = self.sweep()
        self.assertEqual((sw["start_hz"], sw["stop_hz"]), (470_000_000, 514_571_000))
        self.assertIn("514.571 MHz に合わせました", self.win.f_stop.note.text())
        self.type_into(self.win.f_start, "12080")
        sw = self.sweep()
        self.assertEqual((sw["start_hz"], sw["stop_hz"]), (12_100_000_000 - 44_571_000, 12_100_000_000))
        btn = self.win.f_start.note_action
        self.assertTrue(btn.isVisible())
        self.assertEqual(btn.text(), "Span を 20 MHz に縮めて 12.08 GHz〜12.1 GHz にする")
        btn.click()
        self.pump()
        sw = self.sweep()
        self.assertEqual((sw["start_hz"], sw["stop_hz"], sw["span_lock_hz"]),
                         (12_080_000_000, 12_100_000_000, 44_571_000))


class MenuAndKeyTest(_Base):
    def menu_actions(self):
        out = {}
        for top in self.win.menuBar().actions():
            for a in top.menu().actions():
                if not a.isSeparator():
                    out[a.text()] = a
        return out

    def test_menu_items_and_shortcuts(self):
        titles = [a.text() for a in self.win.menuBar().actions()]
        for t in ("ファイル(&F)", "機器(&D)", "校正(&C)", "表示(&V)", "設定(&O)", "ヘルプ(&H)"):
            self.assertIn(t, titles)
        acts = self.win.actions
        expect = {"save": "Ctrl+S", "open": "Ctrl+O", "export": "Ctrl+E", "png_save": "Ctrl+P",
                  "png_copy": "Ctrl+Shift+C", "undo": "Ctrl+Z", "redo": "Ctrl+Y", "run_cont": "F5",
                  "run_single": "F6", "stop": "F7", "pause": "Pause", "mode_sa": "Ctrl+1", "mode_s11": "Ctrl+3",
                  "mode_nf": "Ctrl+5",
                  "marker_peak": "P", "marker_here": "M"}
        for code, key in expect.items():
            self.assertEqual(acts[code].shortcut().toString(), key, code)
        # まだ無い機能は消さずに無効にし、理由をツールチップに出す（U-08）
        for code in ("self_test",):
            self.assertFalse(acts[code].isEnabled(), code)
            self.assertIn("—", acts[code].toolTip(), code)
        # S11（反射）と NF は使える（設計E §6.5・設計L §1.1・設計M）
        self.assertTrue(acts["mode_s11"].isEnabled())
        self.assertTrue(acts["mode_nf"].isEnabled())
        # ボタンのツールチップにもキーを出す（U-12）
        self.assertIn("F5", self.win.btn_cont.toolTip())
        self.assertIn("Ctrl+1", self.win.mode_buttons["sa"].toolTip())

    def test_mode_key(self):
        self.win.plot.setFocus()
        QTest.keyClick(self.win, Qt.Key.Key_4, Qt.KeyboardModifier.ControlModifier)
        self.pump()
        self.assertEqual(self.sweep()["mode"], "sg")

    def test_undo_redo(self):
        """Ctrl+Z / Ctrl+Y は undo / redo を呼ぶ。コアに命令がまだ無ければ何も出さない。"""
        self.set_sweep(start_hz=100_000_000, stop_hz=200_000_000)
        self.type_into(self.win.f_center, "500")
        after = self.sweep()
        self.win.plot.setFocus()
        self.pump(0.1)
        QTest.keyClick(self.win, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.pump()
        has_undo = hasattr(Instrument, "_cmd_undo")
        self.assertNotIn("unknown_command", self.win.st_note.text())
        if has_undo:
            self.assertEqual(self.sweep()["center_hz"], 150_000_000)
            QTest.keyClick(self.win, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
            self.pump()
            self.assertEqual(self.sweep()["center_hz"], after["center_hz"])
        else:
            self.assertEqual(self.sweep()["center_hz"], after["center_hz"])

    def test_ctrl_z_while_typing_goes_to_field(self):
        """打っている途中の Ctrl+Z は欄の文字を戻す。条件の変更は戻さない（キーは欄に渡す: J §4.6）。"""
        self.set_sweep(start_hz=100_000_000, stop_hz=200_000_000)
        v0 = self.api.get_state().result(5)["version"]
        e = self.win.f_center.edit
        e.setFocus()
        e.selectAll()
        QTest.keyClicks(e, "12")
        QTest.keyClick(e, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.pump()
        self.assertEqual(self.api.get_state().result(5)["version"], v0)
        self.assertNotEqual(e.text(), "12")

    def test_letter_key_in_field_is_typed_not_shortcut(self):
        """グラフのキー（M など）は欄にいる間は文字として欄に入る。"""
        e = self.win.f_span.edit
        e.setFocus()
        e.selectAll()
        QTest.keyClicks(e, "5M")
        self.assertEqual(e.text(), "5M")


if __name__ == "__main__":
    unittest.main()
