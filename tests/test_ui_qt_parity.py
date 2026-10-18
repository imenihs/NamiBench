"""Qt の画面: RBW の知らせは RBW の欄に（U-25）と、Web と同じにそろえた所（単位付きの整数の欄・基準の範囲外の帯の
PNG・開いたファイルの見出しと重ねた直後のトレース）。Web の側は tests/test_web_parity.py。
"""
from __future__ import annotations

import numpy as np
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QLabel

from namibench.core import report_layout as rl
from namibench.ui_qt import png_export, texts
from tests.test_screen_parity import write_file
from tests.test_ui_qt_support import UiBase

MHZ = 1_000_000


class QtRbwFieldWarningTest(UiBase):
    def geometry(self):
        c = self.win.rbw_combo
        return c.mapTo(self.win, c.rect().topLeft()), c.size(), self.win.rbw_field.size()

    def test_warning_marks_the_field_and_leaves_the_condition_line(self):
        self.connect_and_sweep(start=495 * MHZ, stop=505 * MHZ)
        combo = self.win.rbw_combo
        self.assertFalse(bool(combo.property("warn")))
        before = self.geometry()
        plain_color = combo.palette().color(combo.foregroundRole()).name()
        self.cmd("set_sweep", rbw_hz=15_000)
        self.wait(lambda: bool(combo.property("warn")), what="RBW の欄が注意の色")
        self.pump(0.2)
        sentence = texts.WARNINGS["rbw15k_unstable"]
        self.assertTrue(combo.toolTip().startswith(sentence), combo.toolTip())
        # 上段の下には出さない
        self.assertNotIn(sentence, self.win.cond_label.text())
        self.assertFalse(self.win.cond_label.isVisible())
        # 欄の文字が注意の色になり、欄の大きさと場所は変わらない
        self.assertEqual(combo.palette().color(combo.foregroundRole()).name().lower(), self.win.pal["warn"].lower())
        self.assertNotEqual(plain_color.lower(), self.win.pal["warn"].lower())
        self.assertEqual(self.geometry(), before)
        # 条件から出たら（180kHz）注意は消える
        self.cmd("set_sweep", rbw_hz=180_000)
        self.wait(lambda: not combo.property("warn"), what="注意が消える")
        self.assertNotIn(sentence, combo.toolTip())
        self.assertEqual(self.geometry(), before)

    def test_zero_span_warning_in_the_tooltip(self):
        self.cmd("connect", backend="simulator", serial="SIM00001")
        self.cmd("set_sweep", center_hz=100 * MHZ, span_hz=0, rbw_hz=15_000)
        combo = self.win.rbw_combo
        self.wait(lambda: bool(combo.property("warn")), what="ゼロスパンの RBW 15kHz")
        self.assertTrue(combo.toolTip().startswith(texts.WARNINGS["rbw15k_zero_span"]), combo.toolTip())
        self.assertNotIn(texts.WARNINGS["rbw15k_zero_span"], self.win.cond_label.text())

    def test_other_condition_lines_stay_under_the_top_bar(self):
        self.connect_and_sweep(start=495 * MHZ, stop=505 * MHZ)
        self.cmd("set_sweep", rbw_hz=15_000)
        self.cmd("set_view", freq_offset_hz=10_000 * MHZ)
        self.wait(lambda: "換算中" in self.win.cond_label.text(), what="換算中の行")
        self.assertNotIn(texts.WARNINGS["rbw15k_unstable"], self.win.cond_label.text())
        self.assertTrue(self.win.rbw_combo.property("warn"))


class QtCountFieldsTest(UiBase):
    """単位付きの整数の欄（QSpinBox の suffix）は 2 つ（回数）。Web の int-field と同じ字。上位 N は数だけ（助数詞を
    付けない: 設計T B-3「{n} 個 → 数だけ」）。"""

    def test_suffix_fields(self):
        from PySide6.QtWidgets import QAbstractSpinBox

        spins = [s for s in self.win.findChildren(QAbstractSpinBox) if s.suffix()]
        names = {s.suffix() for s in spins}
        self.assertEqual(len(spins), 2, [s.objectName() or type(s).__name__ for s in spins])
        self.assertEqual(names, {texts.LABELS["times_suffix"]})
        self.assertEqual(texts.LABELS["count_suffix"], "")
        self.assertEqual(self.win.marker_tab.top_n.text(), "3")
        self.assertEqual(self.win.source_tab.take_avg.text(), "1" + texts.LABELS["times_suffix"])
        self.assertEqual(self.win.source_tab.step.per_step.suffix(), texts.LABELS["times_suffix"])


class QtNormBandPngTest(UiBase):
    """基準の範囲外の帯（設計E §6.4）は画面に出るので、PNG にも同じ区間を同じ色で描く（Web の PNG と同じ）。"""

    def test_png_draws_the_band(self):
        start, stop, n = 490 * MHZ, 510 * MHZ, 501
        step = (stop - start) // (n - 1)
        vals = np.full(n, -3.0)
        vals[:125] = np.nan
        vals[-125:] = np.nan
        data = {"conditions": {"start_hz": start, "stop_hz": stop, "ref_level": 0.0, "db_per_div": 10.0, "unit": "dB",
                               "step_hz": step},
                "normalization": {"name": "TG", "at": "", "applied": True, "before_power_cycle": False,
                                  "out_of_range_hz": [[490 * MHZ, 494_960_000], [505_040_000, 510 * MHZ]]},
                "traces": [{"number": 1, "start_hz": start, "step_hz": step, "values_db": vals, "color": "trace1",
                            "action": "更新", "count": None}],
                "markers": [], "warnings": []}
        size = (1600, 1000)
        img = QImage.fromData(png_export.render_png(data, size=size, palette="dark"), "PNG")
        lay = rl.layout(data, size)
        ax = lay["axes"]
        left, right = ax["plot_left"], ax["plot_right"]
        top = int(ax["plot_top"]) + 20
        bg = QColor(rl.DARK_DEFAULT["graph_bg"])

        def px(hz):
            return int(left + (hz - start) / (stop - start) * (right - left))

        def tinted(image, hz):
            """縦の1列（枠の中の 300 画素）のうち、背景より注意の色（黄）に寄った点の割合（目盛線は数に入らない程度）。"""
            cols = [image.pixelColor(px(hz) + 3, y) for y in range(top, top + 300)]
            return sum(c.red() > bg.red() + 10 and c.green() > bg.green() + 10 and c.blue() <= bg.blue() + 10
                       for c in cols) / len(cols)

        self.assertGreater(tinted(img, 492 * MHZ), 0.9)  # 範囲外の区間（左）
        self.assertGreater(tinted(img, 507 * MHZ), 0.9)  # 範囲外の区間（右）
        self.assertLess(tinted(img, 500 * MHZ), 0.1)  # 基準のある区間
        # 基準を使っていないときは描かない
        data["normalization"] = None
        img2 = QImage.fromData(png_export.render_png(data, size=size, palette="dark"), "PNG")
        self.assertLess(tinted(img2, 492 * MHZ), 0.1)


class QtOpenedFileTest(UiBase):
    def open(self, path):
        self.win.files.open_files([str(path)])
        self.wait(lambda: any(f["path"] == str(path) for f in self.state()["files"]), what="開く")
        self.wait(lambda: all(t["id"] in self.win.trace_tab._file_checks
                              for f in self.state()["files"] for t in f["traces"]), what="一覧の行")
        self.pump(0.3)

    def test_heading_is_the_name_with_details_in_the_tooltip(self):
        self.connect_and_sweep()
        p = write_file(self.root / "a.h5")
        self.open(p)
        f = self.state()["files"][0]
        row = self.win.trace_tab._file_rows[f["file_id"]]
        # 開いたファイルごとに枠で囲む（Web の .file と同じ）: 見出し・［外す］・そのファイルのトレースが枠の中
        self.assertEqual(row.objectName(), "fileBox")
        self.assertEqual(row.contentsRect(), row.rect().adjusted(1, 1, -1, -1))
        for t in f["traces"]:
            self.assertIs(self.win.trace_tab._file_checks[t["id"]].parentWidget(), row)
        name = next(lab for lab in row.findChildren(QLabel))
        self.assertEqual(name.text(), f["name"])
        self.assertEqual(name.toolTip(), texts.file_heading_tip(f))
        self.assertIn("TEST0001", name.toolTip())
        self.assertIn("直結", name.toolTip())

    def test_only_traces_shown_at_save_are_overlaid_first(self):
        self.connect_and_sweep()
        p = write_file(self.root / "a.h5")
        self.open(p)
        f = self.state()["files"][0]
        t1, t2 = (t["id"] for t in f["traces"])
        checks = self.win.trace_tab._file_checks
        self.wait(lambda: t1 in self.win.traces.files and t2 in self.win.traces.files, what="値が届く")
        self.assertTrue(checks[t1].isChecked())
        self.assertFalse(checks[t2].isChecked())
        self.assertTrue(self.win.traces.file_drawn(t1))
        self.assertFalse(self.win.traces.file_drawn(t2))
        # 利用者が出したものは、別のファイルを開いて一覧を作り直しても戻さない（チェックと描画がそろう）
        checks[t2].setChecked(True)
        self.assertTrue(self.win.traces.file_drawn(t2))
        self.open(write_file(self.root / "b.h5"))
        checks = self.win.trace_tab._file_checks
        self.assertTrue(checks[t2].isChecked())
        self.assertTrue(self.win.traces.file_drawn(t2))
