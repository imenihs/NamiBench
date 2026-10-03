"""Qt の画面: ゼロスパンの時間軸と流す表示（設計N §1）。シミュレータの本体で通す。

- 横軸の名前は「時間（推定）」と目盛の単位。範囲は 1回の掃引で 0〜最後の点、流す表示で −長さ〜0
- 表示する長さは右のタブ「表示」の欄（ゼロスパンでない間は理由付きで選べない）
- 時間の位置にマーカーを置け、マーカー表の列は「時間 (ms)」、区間の最大・最小・平均の行が出る
- 周波数を前提にする操作（Center に・マーカーの機能・周波数のオフセット・ピークの一覧）は理由付きで止まる
- マウスの位置の読み値・PNG も時間
"""
import numpy as np

from pygigast.core import report_layout as rl
from pygigast.ui_qt import png_data, png_export, screen_model, texts
from tests.test_ui_qt_support import UiBase

MHZ = 1_000_000
Z = texts.ZERO_SPAN_TEXTS


class ZeroSpanUiTest(UiBase):
    size = (1600, 900)

    def zero_span(self, length=10, sweeps=2):
        self.cmd("connect", backend="simulator", serial="SIM00001")
        self.cmd("set_view", zero_span_s=length)
        self.cmd("set_sweep", center_hz=100 * MHZ, span_hz=0)
        for _ in range(sweeps):
            n0 = self.state()["run"]["sweep_count"]
            self.cmd("run", mode="single")
            self.wait(lambda: self.state()["run"]["state"] == "stopped" and self.state()["run"]["sweep_count"] > n0,
                      what="単発の掃引")
        self.wait(lambda: (self.win.traces.live_payload(1) or {}).get("x_kind") == "time", what="時間軸のトレース")
        self.pump(0.3)

    def x_label(self):
        return self.win.plot.getPlotItem().getAxis("bottom").labelText

    def test_axis_label_and_range(self):
        self.zero_span(length=10)
        self.assertEqual(self.x_label(), Z["axis_time"].replace("{unit}", "s"))
        (x0, x1), _ = self.win.plot.getPlotItem().getViewBox().viewRange()
        self.assertAlmostEqual(x0, -10_000_000, delta=1)
        self.assertAlmostEqual(x1, 0, delta=1)
        self.assertAlmostEqual(self.win.plot.getPlotItem().getAxis("bottom").scale, 1e-6)
        self.win.display_tab.zs_length.setCurrentIndex(self.win.display_tab.zs_length.findData(0))
        self.win.display_tab.zs_length.activated.emit(self.win.display_tab.zs_length.currentIndex())
        self.wait(lambda: (self.win.state.get("view") or {}).get("zero_span_s") == 0, what="1回の掃引にする")
        self.pump(0.3)
        self.assertEqual(self.x_label(), Z["axis_time"].replace("{unit}", "ms"))
        (x0, x1), _ = self.win.plot.getPlotItem().getViewBox().viewRange()
        self.assertAlmostEqual(x0, 0, delta=1)
        self.assertAlmostEqual(x1, 499 * 140, delta=1)
        p = self.win.traces.live_payload(1)
        self.assertEqual(p["axis"]["step_us"], 140.0)
        # 周波数に戻すと軸も戻る
        self.cmd("set_sweep", center_hz=100 * MHZ, span_hz=10 * MHZ)
        self.wait(lambda: self.x_label() != Z["axis_time"].replace("{unit}", "ms"), what="周波数の軸")
        self.assertAlmostEqual(self.win.plot.getPlotItem().getAxis("bottom").scale, 1.0)

    def test_length_field_reason(self):
        self.connect_and_sweep(50 * MHZ, 200 * MHZ)
        f = self.win.display_tab.zs_length
        self.assertFalse(f.isEnabled())
        self.assertIn(Z["not_zero_span"], f.toolTip())
        self.assertEqual([f.itemText(i) for i in range(f.count())], list(texts.ZERO_SPAN_LENGTHS.values()))
        self.zero_span(length=10, sweeps=1)
        self.assertTrue(f.isEnabled())
        self.assertEqual(f.currentData(), 10)

    def test_markers_table_and_reasons(self):
        # 1回の掃引の表示（0〜69.86ms）で確かめる（流す表示はシミュレータの掃引の間隔で点の位置が変わる）
        self.zero_span(length=0)
        self.win._marker_add_at(20_000.0, -30.0)
        self.wait(lambda: any(r["kind"] == "marker" for r in self.win.marker_row_list), what="時間の位置のマーカー")
        self.pump(0.2)
        header = self.win.marker_panel.tree.headerItem()
        self.assertEqual(header.text(2), Z["col_time"])
        self.assertEqual(header.text(5), Z["col_dtime"])
        m = next(r for r in self.win.marker_row_list if r["kind"] == "marker")
        self.assertLessEqual(abs(m["freq_hz"] - 20_000), 140)
        names = [r["name"] for r in self.win.marker_row_list if r["kind"] == "interval"]
        self.assertEqual(names, [Z["row_max"], Z["row_min"], Z["row_avg"]])
        cells = self.win.marker_panel._items[m["id"]].text(2)
        self.assertEqual(cells, f"{m['freq_hz'] / 1e3:.3f}")
        # マーカーのタブ: 時間の欄・周波数のオフセットと機能は理由付きで止まる
        tab = self.win.marker_tab
        self.win.select_marker(m["id"])
        self.pump(0.1)
        self.assertTrue(tab.freq.time_axis)
        self.assertEqual(tab._sel_form.labelForField(tab.freq).text(), Z["time"])
        self.assertTrue(tab.freq.edit.text().endswith(" ms"))
        self.assertFalse(tab.off_type.model().item(0).isEnabled())
        self.assertFalse(tab.func_body.isEnabled())
        self.assertIn(Z["freq_only"], tab.func_body.toolTip())
        self.assertFalse(tab.to_center.isEnabled())
        self.assertFalse(tab.pk_find.isEnabled())
        self.assertEqual(tab.pk_find.toolTip(), Z["freq_only"])
        # 右クリック: Center にはできない・マーカーは置ける（位置は時間）
        menu = self.win._context_menu(10_000.0, -40.0)
        acts = {a.objectName(): a for a in menu.actions()}
        self.assertFalse(acts["ctx_center"].isEnabled())
        self.assertTrue(acts["ctx_marker"].isEnabled())
        self.assertIn("10.000 ms", acts["ctx_marker"].text())
        self.assertFalse(acts["ctx_m_center"].isEnabled())
        self.assertTrue(acts["ctx_m_ref"].isEnabled())
        # マーカーの時間を欄で打つ
        tab.freq.edit.setText("40")
        tab.freq.edit.committed.emit("40")
        self.wait(lambda: abs((next(r for r in self.win.marker_row_list if r["kind"] == "marker")["freq_hz"] or 0)
                              - 40_000) <= 140, what="時間を打って動かす（ms）")

    def test_mouse_reading_and_png(self):
        self.zero_span(length=0, sweeps=1)
        r = screen_model.mouse_reading(self.win.traces.shown_payloads(), 14_000.0, -30.0, True)
        self.assertEqual(r["lines"][0], "14.000 ms")
        self.assertEqual(r["time_us"], 14_000)
        self.cmd("set_xy", x1_hz=10_000, x2_hz=20_000)
        self.pump(0.2)
        data = png_data.build(self.win)
        xa = data["conditions"]["x_axis"]
        self.assertEqual((xa["kind"], xa["x0"], xa["x1"], xa["unit"]), ("time", 0, 499 * 140, "ms"))
        lay = rl.layout(data, (1600, 1000))
        self.assertEqual(lay["x_title"], "時間（推定） (ms)")
        self.assertEqual(lay["table"]["header"][2], "時間 (ms)")
        self.assertEqual(lay["x_labels"][0], "0.000")
        self.assertTrue(any("ゼロスパン 掃引1回" in s for _c, items in lay["band"] for s in items))
        png = png_export.render_png(data, size=(800, 500))
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertEqual(png_export.read_png_metadata(png)["conditions"]["x_axis"]["kind"], "time")

    def test_stream_draws_gaps(self):
        # 流す表示: 測っていない所（掃引の前・掃引の間）は描く値が NaN（線を切る）。描く区切りは −長さ〜0 を覆う
        self.zero_span(length=1, sweeps=3)
        p = self.win.traces.live_payload(1)
        v = np.asarray(p["values"])
        self.assertTrue(np.isnan(v).any())
        self.assertTrue(np.isfinite(v).any())
        self.assertAlmostEqual(p["axis"]["start_us"] - p["axis"]["step_us"] / 2, -1_000_000, delta=1)
