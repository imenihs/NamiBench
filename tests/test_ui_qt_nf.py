"""Qt の NF 測定（設計M）: モードが使える・初めて入ったときのつなぎ方の窓（1回だけ）・信号源のタブの NF の欄（ENR・
ENR の表・増幅度・つなぎ方のボタン）・平均の種類を選べない理由・差の小さい点の印・決めた解像度での崩れ（J §4.11）。"""
from __future__ import annotations

import unittest

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from pygigast.ui_qt import nf_view, texts
from pygigast.ui_qt.plot_layers import marked_points
from tests.qt_layout_check import problems
from tests.test_ui_qt_support import UiBase

MHZ = 1_000_000
N = texts.NF_TEXTS


class NfQtTest(UiBase):
    def enter_nf(self):
        self.cmd("connect", backend="simulator", serial="SIM00001")
        self.cmd("set_sweep", start_hz=400 * MHZ, stop_hz=1600 * MHZ)
        self.api._inst._device.config.nf_hot_db = 11.3
        b = self.win.mode_buttons["nf"]
        self.assertTrue(b.isEnabled())
        b.click()
        self.wait(lambda: self.state()["sweep"]["mode"] == "nf", what="NF モード")
        self.pump(0.2)

    def test_guide_once_and_from_button(self):
        self.enter_nf()
        self.wait(lambda: getattr(self.win, "nf_guide", None) is not None and self.win.nf_guide.isVisible(),
                  what="つなぎ方の窓")
        g = self.win.nf_guide
        self.assertEqual(g.windowTitle(), N["guide_title"])
        self.assertEqual(g.need.text(), N["need_text"])
        self.assertEqual(g.wiring.text(), N["wiring_text"])
        self.assertEqual(g.diagram.blocks, list(texts.NF_WIRING))
        g.btn_close.click()
        self.pump(0.1)
        self.assertFalse(g.isVisible())
        # 2回目からは出さない
        self.win.mode_buttons["sa"].click()
        self.pump(0.2)
        self.win.mode_buttons["nf"].click()
        self.pump(0.4)
        self.assertFalse(g.isVisible())
        # 信号源のタブのボタンから開ける（題はボタンの名前: U-24）
        tab = self.win.source_tab
        self.assertEqual(tab.guide.text(), N["guide_button"])
        tab.guide.click()
        self.pump(0.1)
        self.assertTrue(g.isVisible())
        self.assertEqual(N["guide_button"].rstrip("…"), g.windowTitle())

    def test_enr_field_table_and_amp(self):
        self.enter_nf()
        self.win.nf_guide.close()
        tab = self.win.source_tab
        self.assertTrue(tab.enr_warn.isVisibleTo(tab))
        self.assertEqual(tab.enr_warn.text(), N["enr_needed"])
        self.assertEqual(tab.amp.text(), N["value_none"])
        self.assertEqual(tab.enr_table.text(), N["table_none"])
        self.assertFalse(tab.clear_table.isEnabled())
        # ENR を打つ（dB の小数・Enter で確定: U-13）
        e = tab.enr.edit
        e.setFocus()
        e.selectAll()
        QTest.keyClicks(e, "14.25")
        QTest.keyClick(e, Qt.Key.Key_Return)
        self.wait(lambda: (self.state()["nf"].get("enr") or {}).get("db") == 14.25, what="ENR が入る")
        self.pump(0.2)
        self.assertEqual(tab.enr.edit.text(), "14.25 dB")
        self.assertFalse(tab.enr_warn.isVisibleTo(tab))
        # 表を読み込む（ファイルの中身を送る: Web と同じ命令）
        p = self.root / "enr.csv"
        p.write_text("Frequency (GHz),ENR\n0.01,15.2\n1.0,14.9\n", encoding="utf-8")
        self.open_paths = [str(p)]
        tab.load_table.click()
        self.wait(lambda: (self.state()["nf"].get("table") or {}).get("name") == "enr.csv", what="表")
        self.pump(0.2)
        self.assertEqual(tab.enr_table.text(), nf_view.nf_view(self.state())["table_text"])
        self.assertIn("enr.csv", tab.enr_table.text())
        self.assertFalse(tab.enr.edit.isEnabled())
        self.assertIn(N["enr_table_in_use"], tab.enr.edit.toolTip())
        # いまの範囲（〜1600MHz）の一部が表の外 → 表の行に知らせ（U-25）
        self.assertTrue(tab.table_warn.isVisibleTo(tab))
        self.assertEqual(tab.table_warn.text(), N["table_outside"])
        # 読めない表 → 理由を表の行のそばに（一度きり）
        bad = self.root / "bad.csv"
        bad.write_text("100,15\n100,14\n", encoding="utf-8")
        self.open_paths = [str(bad)]
        tab.load_table.click()
        self.wait(lambda: tab.table_note.isVisibleTo(tab), what="読めない理由")
        self.assertIn("2 行目", tab.table_note.text())
        self.assertEqual(self.state()["nf"]["table"]["name"], "enr.csv")  # 前の表のまま
        tab.clear_table.click()
        self.wait(lambda: self.state()["nf"]["table"] is None, what="表を外す")
        self.pump(0.2)
        self.assertTrue(tab.enr.edit.isEnabled())
        self.assertEqual(tab.enr.edit.text(), "14.25 dB")

    def test_average_type_disabled_with_reason(self):
        self.enter_nf()
        self.win.nf_guide.close()
        self.cmd("set_trace", number=1, action="average", count=4)
        self.pump(0.2)
        row = self.win.trace_tab.rows[1]
        self.assertFalse(row.avg.isEnabled())
        self.assertIn(N["average_type_nf"], row.avg.toolTip())
        self.cmd("set_mode", mode="sa")
        self.pump(0.2)
        self.assertTrue(row.avg.isEnabled())

    def test_unit_field_shows_nf(self):
        self.enter_nf()
        self.win.nf_guide.close()
        self.cmd("nf_set_enr", enr_db=15.0)
        n0 = self.state()["run"]["sweep_count"]
        self.cmd("run", mode="single")
        self.wait(lambda: self.state()["run"]["state"] == "stopped" and self.state()["run"]["sweep_count"] > n0,
                  timeout=20, what="単発")
        self.pump(0.3)
        self.assertEqual(self.win.display_tab.unit.currentText(), N["level_unit_nf_item"])
        self.assertEqual(self.win.display_unit(), "dB")
        # NF の縦軸（既定 REF 20dB・2dB/div: 設計M §1）
        self.assertEqual((self.win.ref_level(), self.win.db_div()), (20.0, 2.0))
        # PNG（画面と同じ縦軸・単位で焼き込む: 設計I）
        from pygigast.ui_qt import png_data, png_export

        data = png_data.build(self.win)
        c = data["conditions"]
        self.assertEqual((c["ref_level"], c["db_per_div"], c["unit"], data["mode"]), (20.0, 2.0, "dB", texts.MODE_NAMES["nf"]))
        self.assertEqual(png_export.render_png(data)[:4], b"\x89PNG")

    def test_small_difference_points_are_dimmed(self):
        n = 5
        pts = {"nf_small_difference": np.array([False, True, False, False, True])}
        m = marked_points({"flags": {"points": pts, "transient_head_points": 0}}, n)
        self.assertEqual(m.tolist(), pts["nf_small_difference"].tolist())


class NfQtLayoutTest(UiBase):
    """NF の欄とつなぎ方の窓を、決めた解像度で開いて数える（tests/test_ui_qt_sizes と同じ検査）。"""

    size = (1280, 720)

    def test_source_tab_and_guide(self):
        found = []
        self.cmd("connect", backend="simulator", serial="SIM00001")
        self.cmd("set_sweep", start_hz=400 * MHZ, stop_hz=1600 * MHZ)
        self.cmd("set_mode", mode="nf")
        self.wait(lambda: getattr(self.win, "nf_guide", None) is not None and self.win.nf_guide.isVisible(),
                  what="つなぎ方の窓")
        self.pump(0.2)
        found += problems(self.win.nf_guide, "NF 測定のつなぎ方")
        self.win.nf_guide.close()
        for case in ("ENR なし", "表"):
            if case == "表":
                self.cmd("nf_load_enr_table", text="500,15\n1000,14\n", file_name="346B_ENR.csv")
            self.win.show_part("tab-source")
            self.win.source_tab.flash_table_note(texts.ERRORS["enr_table_unreadable"])
            self.pump(0.3)
            found += problems(self.win, f"信号源 NF（{case}）")
        self.assertEqual(sorted(set(found)), [])


class NfQtLayoutWideTest(NfQtLayoutTest):
    size = (1920, 1080)


if __name__ == "__main__":
    unittest.main()
