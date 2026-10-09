"""トレースの描き方・振幅（コアが正本）・右のタブ・状態バーを確かめる（設計F §5・J §4.1・§4.5・E §6.4・§6.6）。"""
import time
import unittest

import numpy as np

from pygigast.ui_qt import texts
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
        # 負号は見間違えない字形「−」で出す（J §4.1）
        self.assertEqual(self.win.f_ref.edit.text(), "−37.0 dBm")

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
        self.type_count(row, "8")
        self.assertEqual(self.state()["traces"][0]["count"], 8)
        # 平均は回数が要るので、一覧に「回数なし」が無い
        self.assertEqual(row.count.findText("回数なし"), -1)
        j = row.avg.findData("log")
        row.avg.setCurrentIndex(j)
        row.avg.activated.emit(j)
        self.pump()
        self.assertEqual(self.state()["traces"][0]["average_type"], "log")
        # 対数平均を選んだらその場で知らせる（F §5.2.1・U-02）
        self.assertIn("2.5dB", row.note.text())

    def type_count(self, row, text):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        e = row.count.lineEdit()
        e.setFocus()
        self.pump(0.1)
        e.selectAll()
        # QTest.keyClicks は日本語の文字を渡すと Python ごと止まる（PySide6）。打ったのと同じく欄へ入れる
        e.insert(text)
        QTest.keyClick(e, Qt.Key.Key_Return)
        self.pump()

    def test_hold_count_dropdown_and_typing(self):
        """最大保持の回数: 一覧から選ぶ・打つ・「回数なし」を選ぶ・読めない値は戻して知らせる。"""
        self.cmd("set_trace", number=2, action="max_hold", visible=True)
        self.pump()
        row = self.win.trace_tab.rows[2]
        i = row.count.findData(64)
        row.count.choose(i)
        self.pump()
        self.assertEqual(self.state()["traces"][1]["count"], 64)
        self.assertEqual(row.count.lineEdit().text(), "64 回")
        self.type_count(row, "100")
        self.assertEqual(self.state()["traces"][1]["count"], 100)
        self.assertEqual(row.count.lineEdit().text(), "100 回")
        i = row.count.findText("回数なし")
        self.assertGreaterEqual(i, 0)
        row.count.choose(i)
        self.pump()
        self.assertIsNone(self.state()["traces"][1]["count"])
        self.assertEqual(row.count.lineEdit().text(), "回数なし")
        self.type_count(row, "64")
        self.type_count(row, "回数なし")  # 打っても「回数なし」になる
        self.assertIsNone(self.state()["traces"][1]["count"])
        self.type_count(row, "abc")
        self.assertIsNone(self.state()["traces"][1]["count"])
        self.assertEqual(row.count.lineEdit().text(), "回数なし")
        self.assertIn("1〜10000", row.note.text())

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
    def test_layout_choice_and_amplitude_detail(self):
        d = self.win.display_tab
        # 表示の組み合わせ（設計P §2: 1操作で切り替える。コアの表示の設定）
        self.assertTrue(d.view_spectrum.isChecked())
        for w in (d.view_waterfall, d.view_density):
            self.assertTrue(w.isEnabled())
        d.view_waterfall.click()
        self.wait(lambda: (self.state()["view"] or {}).get("layout") == "waterfall", what="＋ウォーターフォール")
        self.wait(lambda: self.win.parts.lower["waterfall"].holder.isVisible(), what="ウォーターフォールを出す")
        self.assertFalse(self.win.parts.lower["density"].holder.isVisible())
        d.view_spectrum.click()
        self.wait(lambda: (self.state()["view"] or {}).get("layout") == "spectrum", what="スペクトルのみ")
        # 単位・外部補正・配色は作った（設計O・J §4.4）ので使える
        for w in (d.ext, d.colors, d.unit, d.reset_light, d.reset_dark):
            self.assertTrue(w.isEnabled())
        # 4つ目は基準との比を出している間に欄が出す値（選ぶものではないのでいつも選べない: J §4.10）
        self.assertEqual([d.unit.itemText(i) for i in range(d.unit.count())],
                         ["dBm", "dBμV", "dBmV", texts.NF_TEXTS["level_unit_nf_item"],
                          texts.VIEW_TEXTS["level_unit_ratio_item"]])
        self.assertFalse(d.unit.model().item(3).isEnabled())


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
        # 状態バーの知らせは最後の1つだけを出す。取った直後に比の表示に変わった知らせが続くと、待つ間に上書きされて
        # 見えないことがあった（まとめて回したときだけ落ちた）。出した知らせを全部覚えて確かめる
        notes = []
        show = self.win.status.show_note
        self.win.status.show_note = lambda text, folder=None: (notes.append(text), show(text, folder))
        src.take_fine.click()
        self.wait(lambda: any("基準を取りました" in n for n in notes), timeout=15, what="基準を取った知らせ")
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


class ImageRejectChoiceTest(UiBase):
    """イメージ除去の3つの選択（設計F §7.4）: 名前「イメージ除去」・選択肢に1画面の見積もり・一度出す注意。"""

    def choose(self, code):
        c = self.win.trace_tab.image_reject
        i = c.findData(code)
        c.setCurrentIndex(i)
        c.activated.emit(i)
        self.pump()

    def items(self):
        c = self.win.trace_tab.image_reject
        return [(c.itemData(i), c.itemText(i)) for i in range(c.count())]

    def test_items_show_estimates_and_follow_conditions(self):
        tab = self.win.trace_tab
        self.cmd("set_sweep", start_hz=0, stop_hz=1_000_000_000)
        self.pump()
        # 設計F §7.4 の例と同じ: 0〜1GHz で 正確 約 3.8 秒・スピード 約 1.9 秒・なし 約 1.0 秒
        self.assertEqual(self.items(), [("accurate", "正確（約 3.8 秒）"), ("fast", "スピード（約 1.9 秒）"),
                                        ("off", "なし（約 1.0 秒）")])
        self.assertEqual(tab.image_reject.currentData(), self.state()["sweep"]["image_reject"])
        form = tab.image_reject.parentWidget().layout()
        self.assertEqual(form.labelForField(tab.image_reject).text(), texts.LABELS["image_reject"])
        width = tab.image_reject.width()
        # 条件を変えたら文字も変える（1 秒未満も小数1桁）。欄の幅は動かさない
        self.cmd("set_sweep", start_hz=100_000_000, stop_hz=110_000_000)
        self.pump()
        est = self.state()["sweep"]["image_reject_estimates"]
        want = [(c, texts.image_reject_item(c, est[c])) for c in ("accurate", "fast", "off")]
        self.assertEqual(self.items(), want)
        self.assertRegex(self.items()[2][1], r"^なし（約 0\.\d 秒）$")
        self.assertEqual(tab.image_reject.width(), width)

    def test_note_once_when_accurate_or_fast_chosen(self):
        """正確・スピードを選んだときだけ、短い信号が消えることを一度出す（ずっとは出さない）。ツールチップにも置く。"""
        tab = self.win.trace_tab
        c = tab.image_reject
        self.assertIn("短い時間だけ出る信号は消えます", c.toolTip())
        self.assertIn("『なし』と最大保持", c.toolTip())
        self.choose("off")
        self.assertEqual(self.state()["sweep"]["image_reject"], "off")
        self.assertTrue(tab.ic_note.isHidden())
        self.choose("accurate")
        self.assertEqual(self.state()["sweep"]["image_reject"], "accurate")
        self.assertFalse(tab.ic_note.isHidden())
        self.assertEqual(tab.ic_note.text(), texts.LABELS["image_reject_short"])
        self.choose("off")
        self.assertTrue(tab.ic_note.isHidden())
        self.choose("fast")
        self.assertFalse(tab.ic_note.isHidden())
        self.choose("off")
        # 状態を映すだけ（ほかの口で選んだ）では出さない
        self.cmd("set_sweep", image_reject="accurate")
        self.pump()
        self.assertEqual(c.currentData(), "accurate")
        self.assertTrue(tab.ic_note.isHidden())

    def test_disabled_with_reason_in_nf_and_zero_span(self):
        c = self.win.trace_tab.image_reject
        self.assertTrue(c.isEnabled())
        self.cmd("set_mode", mode="nf")
        self.pump()
        self.assertFalse(c.isEnabled())
        self.assertIn(texts.REASON_TEXTS["image_reject_nf"], c.toolTip())
        self.cmd("set_mode", mode="sa")
        self.cmd("set_sweep", span_hz=0)
        self.pump()
        self.assertFalse(c.isEnabled())
        self.assertIn(texts.REASON_TEXTS["image_reject_zero_span"], c.toolTip())


class TraceLineQtTest(UiBase):
    """トレースの線（設計F §9）: 2段目のクリアの真下のドロップダウン・線のペン・重なりの順・凡例の見本・PNG。"""

    def choose(self, n, code):
        c = self.win.trace_tab.rows[n].line
        i = c.findData(code)
        c.setCurrentIndex(i)
        c.activated.emit(i)
        self.pump()

    def test_dropdown_under_clear(self):
        tab = self.win.trace_tab
        grid = tab.g_traces.layout()
        for r in tab.rows.values():
            self.assertEqual([(r.line.itemData(i), r.line.itemText(i)) for i in range(r.line.count())],
                             list(texts.TRACE_LINES.items()))
            self.assertEqual(r.line.currentData(), "solid")  # 初期値は実線
            rl_, cl, _rs, _cs = grid.getItemPosition(grid.indexOf(r.line))
            rc, cc, _rs, _cs = grid.getItemPosition(grid.indexOf(r.clear))
            self.assertEqual((rl_, cl), (rc + 1, cc))  # クリアの真下
        self.pump(0.2)
        widths = {r.line.width() for r in tab.rows.values()}
        self.assertEqual(len(widths), 1)  # 列の幅は1つ（一番長い名前とクリアの広い方）
        self.assertGreaterEqual(widths.pop(), max(r.clear.width() for r in tab.rows.values()))

    def test_pen_order_legend_and_accumulation(self):
        self.cmd("set_trace", number=2, action="average", count=4, visible=True)
        self.connect_and_sweep()
        self.wait(lambda: self.win.traces.live_payload(2) is not None, what="トレース2")
        before = self.state()["traces"][1]["current_count"]
        self.assertEqual(before, 1)  # 単発は1画面（設計F §5.2.1）
        layer = self.win.traces
        self.choose(2, "thick")
        self.choose(1, "thin")
        st = {t["number"]: t for t in self.state()["traces"]}
        self.assertEqual((st[1]["line"], st[2]["line"]), ("thin", "thick"))
        self.assertEqual(st[2]["current_count"], before)  # 線を変えても蓄積は捨てない
        self.wait(lambda: layer.line_of(2) == "thick" and layer.line_of(1) == "thin", what="線が画面に届く")
        c1, c2 = layer.live[1]["curve"], layer.live[2]["curve"]
        self.assertEqual(c2.opts["pen"].widthF(), 2.0)
        # 細線（0.5 画素）は 1 画素の線を幅の割合だけ薄く描く
        self.assertEqual(c1.opts["pen"].widthF(), 1.0)
        self.assertAlmostEqual(c1.opts["pen"].color().alphaF(), 0.5, delta=0.01)
        self.assertEqual(c2.opts["pen"].color().alphaF(), 1.0)
        # 太い線を上に（番号の小さい 1 が細線でも、2 の太線が上）。参照トレースより上
        self.assertGreater(c2.zValue(), c1.zValue())
        self.choose(1, "thick")
        self.choose(2, "solid")
        self.wait(lambda: layer.line_of(1) == "thick", what="線が画面に届く")
        self.assertGreater(c1.zValue(), c2.zValue())
        self.choose(1, "dash")
        self.wait(lambda: layer.line_of(1) == "dash", what="線が画面に届く")
        pen = c1.opts["pen"]
        self.assertEqual(pen.dashPattern(), [8.0, 4.0])
        self.assertGreater(c2.zValue(), c1.zValue())  # 破線と実線は同じ段（番号順: 2 が上）
        # 凡例の見本は曲線のペン（線を変えたら作り直す）
        samples = [it[0] for it in layer.legend.items]
        self.assertEqual([s.item for s in samples], [c1, c2])
        self.assertEqual(samples[0].item.opts["pen"].dashPattern(), [8.0, 4.0])
        # PNG の中身にも線が入る（PNG は画面と同じ線で描く: I §3）
        from pygigast.ui_qt import png_data

        data = png_data.build(self.win)
        self.assertEqual([(t["number"], t["line"]) for t in data["traces"]], [(1, "dash"), (2, "solid")])
        self.assertEqual([it["line"] for it in png_data.rl.layout(data, (1600, 1000))["legend"]], ["dash", "solid"])

    def test_line_comes_back_from_core(self):
        """別の口（Web など）で変えた線も、欄とグラフに映る。"""
        self.cmd("set_trace", number=3, line="dot", visible=True)
        self.pump()
        self.assertEqual(self.win.trace_tab.rows[3].line.currentData(), "dot")
        self.assertEqual(self.win.traces.line_of(3), "dot")
