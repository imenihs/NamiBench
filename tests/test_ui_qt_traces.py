"""トレースの描き方・振幅（コアが正本）・右のタブ・状態バーを確かめる（設計F §5・J §4.1・§4.5・E §6.4・§6.6）。"""
import time
import unittest

import numpy as np

from tests.test_ui_qt_support import UiBase


def legend_texts(win):
    return [s for _c, s, _p in win.traces.legend_entries()]


class AmplitudeTest(UiBase):
    def test_core_amplitude_is_shown(self):
        """REF・dB/div はコアの状態が正本。別の口から変えても画面の縦軸が合う。"""
        self.cmd("set_amplitude", ref_level=-37.0, db_per_div=5)
        self.pump()
        self.assertAlmostEqual(self.win.ref_level(), -37.0)
        self.assertEqual(self.win.db_div(), 5.0)
        lo, hi = self.win.plot.getPlotItem().getViewBox().viewRange()[1]
        self.assertAlmostEqual(hi, -37.0, delta=0.05)
        self.assertAlmostEqual(lo, -87.0, delta=0.05)
        self.assertEqual(self.win.div_combo.currentData(), 5.0)
        self.assertEqual(self.win.f_ref.edit.text(), "-37.0 dBm")

    def test_div_combo_sends_to_core(self):
        i = self.win.div_combo.findData(2.0)
        self.win.div_combo.setCurrentIndex(i)
        self.win.div_combo.activated.emit(i)
        self.pump()
        self.assertEqual(self.state()["amplitude"]["db_per_div"], 2)

    def test_zero_db_line_when_unit_is_db(self):
        self.cmd("set_amplitude", unit="dB")
        self.pump()
        self.assertTrue(self.win.traces.zero_line.isVisible())
        self.assertIn("dB", self.win.f_ref.edit.text())
        self.cmd("set_amplitude", unit="dBm")
        self.pump()
        self.assertFalse(self.win.traces.zero_line.isVisible())


class TraceDrawTest(UiBase):
    def test_traces_by_number_and_legend(self):
        self.cmd("set_trace", number=2, visible=True, action="max_hold", count=5)
        self.cmd("set_trace", number=1, action="average", count=4)
        self.connect_and_sweep()
        self.wait(lambda: self.win.traces.live_payload(2) is not None, what="トレース2")
        self.pump(0.3)
        c1 = self.win.traces.live[1]["curve"]
        c2 = self.win.traces.live[2]["curve"]
        self.assertGreater(len(c1.yData), 100)
        self.assertGreater(len(c2.yData), 100)
        # 色は J §4.4 のトレース1・2
        self.assertEqual(c1.opts["pen"].color().name(), self.win.pal["trace1"])
        self.assertEqual(c2.opts["pen"].color().name(), self.win.pal["trace2"])
        leg = legend_texts(self.win)
        self.assertTrue(any(s.startswith("1 平均 パワー ") and s.endswith("/4") for s in leg), leg)
        self.assertTrue(any(s.startswith("2 最大保持 ") and s.endswith("/5") for s in leg), leg)
        # 表示をやめると線を消す（処理は続く: F §5.1）
        self.win.trace_tab.rows[2].visible.setChecked(False)
        self.pump()
        self.assertFalse(self.state()["traces"][1]["visible"])
        self.assertEqual(len(self.win.traces.live[2]["curve"].yData or []), 0)
        self.assertEqual(len(legend_texts(self.win)), 1)

    def test_nan_gap_and_marked_points(self):
        n = 50
        vals = np.full(n, -60.0, dtype=np.float32)
        vals[20:25] = np.nan
        oc = np.zeros(n, dtype=bool)
        oc[40:43] = True
        self.win._on_event("trace", {"trace": 1, "source": "live", "unit": "dBm", "action": "update",
                                     "axis": {"start_hz": 100_000_000, "step_hz": 1_000_000, "points": n},
                                     "values": vals, "flags": {"transient_head_points": 3, "out_of_cal_range": oc}})
        e = self.win.traces.live[1]
        y = e["curve"].yData
        self.assertTrue(np.isnan(y[20:25]).all())   # 値なしは線を切る（数値を作らない: E §6.4）
        m = e["mark"].yData
        finite = np.flatnonzero(np.isfinite(m))
        # 前置き不足の 0〜2 と校正の範囲外の 40〜42 を、隣の点まで控えめな色で
        self.assertEqual(finite.tolist(), [0, 1, 2, 3, 39, 40, 41, 42, 43])
        self.assertEqual(e["mark"].opts["pen"].color().name(), self.win.pal["text_dim"])

    def test_fill_style(self):
        self.connect_and_sweep()
        self.win.display_tab.style_fill.setChecked(True)
        self.pump()
        c = self.win.traces.live[1]["curve"]
        self.assertIsNotNone(c.opts["fillLevel"])
        self.assertAlmostEqual(c.opts["fillLevel"], self.win.ref_level() - 10 * self.win.db_div())
        self.win.display_tab.style_line.setChecked(True)
        self.pump()
        self.assertIsNone(c.opts["fillLevel"])


class TraceTabTest(UiBase):
    def test_action_average_sets_default_count(self):
        row = self.win.trace_tab.rows[1]
        i = row.action.findData("average")
        row.action.setCurrentIndex(i)
        row.action.activated.emit(i)
        self.pump()
        t = self.state()["traces"][0]
        self.assertEqual((t["action"], t["count"]), ("average", 16))
        self.assertTrue(row.count.isEnabled())
        self.assertTrue(row.avg.isEnabled())
        row.count.setValue(8)
        self.pump()
        self.assertEqual(self.state()["traces"][0]["count"], 8)
        j = row.avg.findData("log")
        row.avg.setCurrentIndex(j)
        row.avg.activated.emit(j)
        self.pump()
        self.assertEqual(self.state()["traces"][0]["average_type"], "log")
        # 対数平均を選んだらその場で知らせる（F §5.2.1・U-02）
        self.assertIn("2.5dB", row.note.text())

    def test_update_disables_count_with_reason(self):
        row = self.win.trace_tab.rows[3]
        self.assertFalse(row.count.isEnabled())
        self.assertIn("回数は", row.count.toolTip())
        self.assertFalse(row.avg.isEnabled())
        vec = row.avg.model().item(row.avg.findData("vector"))
        self.assertFalse(vec.isEnabled())

    def test_reset_notice_on_row(self):
        self.cmd("set_trace", number=1, action="max_hold")
        self.connect_and_sweep()
        self.cmd("set_sweep", start_hz=100_000_000, stop_hz=300_000_000)
        self.wait(lambda: "リセット" in self.win.trace_tab.rows[1].note.text(), what="リセットの知らせ")
        self.assertIn("周波数軸", self.win.trace_tab.rows[1].note.text())


class DisplayTabTest(UiBase):
    def test_later_features_disabled_with_reason(self):
        d = self.win.display_tab
        for w in (d.view_waterfall, d.view_density, d.ext, d.colors):
            self.assertFalse(w.isEnabled())
            self.assertIn("次の回", w.toolTip())
        self.assertTrue(d.view_spectrum.isChecked())
        self.assertFalse(d.unit.model().item(1).isEnabled())


class StatusTest(UiBase):
    def test_sg_output_and_task_count(self):
        self.cmd("connect", backend="simulator", serial="SIM00001")
        self.cmd("set_mode", mode="sg", sg_hz=500_000_000)
        self.cmd("run", mode="continuous")
        self.wait(lambda: "SG 出力中" in self.win.st_src.text(), what="SG 出力中")
        self.assertIn("500 MHz", self.win.st_src.text())
        self.cmd("stop")
        self.wait(lambda: not self.win.st_src.isVisible(), what="止めたら消える")

    def test_tg_normalization_status_and_reference(self):
        """TG で基準なし → 受けた電力と理由。細かく取る → 基準の名前と dB の縦軸・0dB の線（E §6.2〜§6.6）。"""
        self.cmd("connect", backend="simulator", serial="SIM00001")
        self.cmd("set_mode", mode="tg")
        self.cmd("set_sweep", start_hz=100_000_000, stop_hz=300_000_000)
        self.cmd("run", mode="single")
        self.wait(lambda: "基準なし" in self.win.st_norm.text(), what="基準なしの表示")
        self.assertIn("基準を選んでいません", self.win.st_norm.text())
        self.assertIn("受けた電力", self.win.plot.getPlotItem().getAxis("left").labelText)
        src = self.win.source_tab
        self.assertTrue(src.take_fine.isEnabled())
        src.take_name.setText("直結")
        src.take_fine.click()
        self.wait(lambda: "基準を取りました" in self.win.st_note.text(), timeout=15, what="基準を取った知らせ")
        self.wait(lambda: self.win.st_norm.text().startswith("基準: 直結"), what="基準の名前")
        self.wait(lambda: self.win.display_unit() == "dB", what="比の単位")
        self.assertTrue(self.win.traces.zero_line.isVisible())
        self.wait(lambda: src.ref_list.count() == 1, what="基準の一覧")
        self.assertIn("直結", src.ref_list.item(0).text())

    def test_source_tab_disabled_outside_tg(self):
        src = self.win.source_tab
        self.cmd("connect", backend="simulator", serial="SIM00001")
        self.pump()
        self.assertFalse(src.take_wide.isEnabled())
        self.assertIn("TG・S11", src.take_wide.toolTip())
        self.assertIn("未取得", src.ref_state.text())


class TaskPanelTest(UiBase):
    def test_panel_shows_after_one_second_and_cancel(self):
        p = self.win.task_panel
        t = {"task_id": "abc", "kind": "export_csv", "started_at": time.time(), "done": 10, "total": 100,
             "elapsed_s": 0.2, "cancelling": False, "detail": {}}
        p.apply_tasks([t])
        self.assertFalse(p.isVisible())   # 1秒未満は何も出さない（U-19）
        t2 = dict(t, started_at=time.time() - 2, elapsed_s=2.0)
        p.apply_tasks([t2])
        self.assertTrue(p.isVisible())
        row = p._rows["abc"]
        self.assertIn("書き出しています", row.label.text())
        self.assertIn("10/100", row.label.text())
        self.assertEqual(row.bar.maximum(), 100)
        row.cancel.click()     # もう無い処理なら何も出さない
        self.pump()
        p.apply_tasks([])
        self.assertFalse(p.isVisible())


if __name__ == "__main__":
    unittest.main()
