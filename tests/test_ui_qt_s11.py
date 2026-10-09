"""Qt の S11（反射。設計E §6.5・設計R §3・§5.1・設計J §4.2・§4.5）の画面を確かめる。

- S11 のモードが使える（ボタン・メニュー・キー）
- 信号源のタブ: オープン → ショートを1段ずつ（次に押すボタンを強調・ショートはオープンの後・測ったオープンの文）
- 上段のボタンは S11 では次の1段（オープンを測る → ショートを測る）
- 縦軸の表示の形式（表示のタブの「S11 の表示」。縦軸に1つ）: 位相系は無効にして理由、S11 でないときは欄ごと理由付きで
  無効、トレースの行には形式の欄が無い
- |Γ|・SWR の縦軸: 1目盛の欄の名前と選択肢・REF の欄は無効にして理由・目盛の数字
- 縦軸と単位の違う固定のトレースは描かず、「表示」のチェックを無効にして理由はツールチップだけ（行に文を書かない）
"""
from __future__ import annotations

import unittest

import numpy as np

from pygigast.core.trace import formats
from pygigast.ui_qt import screen_s11, texts
from tests.test_ui_qt_support import UiBase

MHZ = 1_000_000


class S11UiBase(UiBase):
    def s11_ready(self):
        self.cmd("connect", backend="simulator", serial="SIM00001")
        self.cmd("set_sweep", start_hz=100 * MHZ, stop_hz=200 * MHZ)
        self.cmd("set_mode", mode="s11")
        self.wait(lambda: (self.win.state.get("sweep") or {}).get("mode") == "s11", what="S11 モード")

    def take(self, standard, **kw):
        self.cmd("reference_take", standard=standard, **kw)
        self.wait(lambda: not self.state()["tasks"], timeout=20, what=f"{standard} を測る")
        self.pump(0.3)

    def reference(self):
        self.take("open", kind="fine")
        self.take("short")
        self.wait(lambda: self.state()["display_quantity"] == "ratio", timeout=20, what="基準との比")
        self.wait(lambda: self.win.traces.live_payload(1) is not None, what="トレース1")
        self.pump(0.3)


class ModeAndSourceTabTest(S11UiBase):
    def test_mode_enabled(self):
        self.assertTrue(self.win.mode_buttons["s11"].isEnabled())
        self.assertTrue(self.win.actions["mode_s11"].isEnabled())
        self.assertEqual(self.win.actions["mode_s11"].shortcut().toString(), "Ctrl+3")

    def test_open_then_short_step_by_step(self):
        src = self.win.source_tab
        self.cmd("connect", backend="simulator", serial="SIM00001")
        self.pump()
        # S11 でないときは使えない（理由をツールチップに: U-08）
        self.assertFalse(src.s11_open.isEnabled())
        self.assertIn(texts.REASON_TEXTS["s11_mode_only"], src.s11_open.toolTip())
        self.s11_ready()
        self.pump(0.2)
        self.assertTrue(src.s11_open.isEnabled())
        self.assertFalse(src.s11_short.isEnabled())
        self.assertIn(texts.REASON_TEXTS["s11_needs_open"], src.s11_short.toolTip())
        # TG の取り方は S11 では使わない（2段で取る）
        self.assertFalse(src.take_wide.isEnabled())
        self.assertIn(texts.REASON_TEXTS["tg_take_in_s11"], src.take_wide.toolTip())
        # 次に押すボタンを強調する（1段ずつ）
        self.assertTrue(src.s11_open.property("primary"))
        self.assertFalse(bool(src.s11_short.property("primary")))
        src.s11_range.setCurrentIndex(src.s11_range.findData("fine"))
        src.s11_open.click()
        self.wait(lambda: src.s11_short.isEnabled(), timeout=20, what="ショートを測れる")
        self.assertTrue(src.s11_short.property("primary"))
        self.assertFalse(bool(src.s11_open.property("primary")))
        self.assertTrue(src.s11_state.isVisible())
        self.assertIn(texts.LABELS["s11_range_fine"], src.s11_state.text())
        self.assertIn(texts.LABELS["s11_range_fine"], src.s11_short.toolTip())
        src.take_name.setText("アンテナ")
        src.s11_short.click()
        self.wait(lambda: (self.state()["normalization"].get("selected") or {}).get("name") == "アンテナ",
                  timeout=20, what="基準ができる")
        self.wait(lambda: not src.s11_state.isVisible(), what="オープンの文が消える")
        self.wait(lambda: src.ref_list.count() == 1, what="基準の一覧")
        self.assertIn("S11", src.ref_list.item(0).text())

    def test_quick_button_is_the_next_step(self):
        q = self.win.ref_quick
        self.s11_ready()
        self.wait(lambda: q.isVisible(), what="上段のボタン")
        self.assertEqual(q.button.text(), texts.S11_QUICK_LABELS["open"])
        self.assertTrue(q.button.isEnabled())
        q.button.click()
        self.wait(lambda: q.button.text() == texts.S11_QUICK_LABELS["short"], timeout=20, what="次はショート")
        self.assertTrue(q.status.text().startswith("オープン "))
        q.button.click()
        self.wait(lambda: (self.state()["normalization"].get("selected") or {}).get("mode") == "s11", timeout=20,
                  what="S11 の基準")
        self.assertTrue(self.state()["normalization"]["selected"]["name"].startswith("S11 "))
        self.wait(lambda: q.button.text() == texts.S11_QUICK_LABELS["open"], what="次はまたオープン")


class FormatAndAxisTest(S11UiBase):
    def format_items(self):
        c = self.win.display_tab.axis_format
        m = c.model()
        return {c.itemData(i): (m.item(i).isEnabled(), m.item(i).toolTip(), c.itemText(i)) for i in range(c.count())}

    def choose_format(self, code):
        c = self.win.display_tab.axis_format
        c.setCurrentIndex(c.findData(code))
        c.activated.emit(c.currentIndex())
        self.wait(lambda: self.state()["view"]["s11_format"] == code, what=f"形式 {code} がコアへ")

    def test_format_field_is_one_for_the_axis(self):
        self.cmd("connect", backend="simulator", serial="SIM00001")
        self.cmd("set_mode", mode="tg")
        self.pump(0.3)
        tab = self.win.display_tab
        items = self.format_items()
        self.assertEqual(list(items), list(formats.FORMATS))
        for code in formats.VECTOR_FORMATS:
            self.assertFalse(items[code][0])
            self.assertEqual(items[code][1], texts.REASON_TEXTS["format_vector"])
        self.assertEqual(items["db"][2], texts.S11_FORMAT_NAMES["db"])
        self.assertTrue(items["swr"][0] and items["gamma"][0])
        # S11 でもなく S11 のトレースも無いときは、欄ごと理由付きで無効（U-08）
        self.assertFalse(tab.axis_format.isEnabled())
        self.assertIn(texts.REASON_TEXTS["format_s11_context"], tab.axis_format.toolTip())
        # 単位の欄と同じ列で同じ幅（S-02）
        self.assertEqual(tab.axis_format.width(), tab.unit.width())
        # トレースの行に形式の欄は無い
        self.assertFalse(hasattr(self.win.trace_tab.rows[1], "fmt"))
        self.assertFalse(hasattr(self.win.trace_tab.rows[1], "unit_note"))
        self.cmd("set_mode", mode="s11")
        self.wait(lambda: tab.axis_format.isEnabled(), what="S11 で選べる")
        self.choose_format("gamma")
        # 別の画面（命令）から変えても欄が映す
        self.cmd("set_view", s11_format="swr")
        self.wait(lambda: tab.axis_format.currentData() == "swr", what="欄が SWR を映す")

    def test_swr_axis_and_fixed_trace_of_another_unit(self):
        self.s11_ready()
        # 基準を取る前の電力（dBm）の値でトレース2を固定しておく
        self.cmd("set_trace", number=2, visible=True, action="update")
        self.cmd("run", mode="single")
        self.wait(lambda: (self.win.traces.live_payload(2) or {}).get("unit") == "dBm", timeout=10, what="トレース2")
        self.cmd("set_trace", number=2, action="fixed")
        self.reference()
        self.choose_format("swr")
        self.wait(lambda: self.win.display_unit() == "SWR", what="縦軸が SWR")
        self.pump(0.3)
        # 1目盛の欄は SWR/div、REF の欄は使えない（下端が 1 に決まっている）
        self.assertEqual(self.win.div_field.name_label.text(), "SWR/div")
        self.assertEqual([self.win.div_combo.itemData(i) for i in range(self.win.div_combo.count())],
                         list(formats.LINEAR_AXES["SWR"]["choices"]))
        self.assertFalse(self.win.f_ref.isEnabled())
        self.assertIn("SWR", self.win.f_ref.toolTip())
        lo, hi = self.win.plot.getPlotItem().getViewBox().viewRange()[1]
        self.assertAlmostEqual(lo, 1.0, delta=0.01)
        self.assertAlmostEqual(hi, 3.0, delta=0.01)
        self.assertFalse(self.win.btn_ref_auto.isEnabled())
        # 1目盛を変えるとコアへ送り、上端が変わる
        i = self.win.div_combo.findData(0.5)
        self.win.div_combo.setCurrentIndex(i)
        self.win.div_combo.activated.emit(i)
        self.wait(lambda: self.state()["amplitude"]["swr_per_div"] == 0.5, what="1目盛がコアへ")
        self.wait(lambda: abs(self.win.ref_level() - 6.0) < 1e-9, what="上端 6")
        # 描いた値は SWR（1 以上）
        y = np.asarray(self.win.traces.live[1]["curve"].yData, dtype=float)
        self.assertGreaterEqual(float(np.nanmin(y)), 1.0)
        # 固定したトレース2（dBm）は縦軸と単位が違うので描かず、「表示」のチェックを無効にして理由はツールチップだけ
        r2 = self.win.trace_tab.rows[2]
        self.wait(lambda: not r2.visible.isEnabled(), timeout=10, what="表示のチェックが無効")
        self.assertIn("SWR", r2.visible.toolTip())
        self.assertIn("dBm", r2.visible.toolTip())
        self.assertTrue(r2.visible.isChecked())
        self.assertIsNone(self.win.traces.live_payload(2))
        self.assertEqual(len(self.win.traces.live[2]["curve"].yData or []), 0)
        self.assertFalse([w for w in r2.note.parent().findChildren(type(r2.note)) if w.isVisible()
                          and "描きません" in w.text()])
        self.assertTrue(self.win.trace_tab.rows[1].visible.isEnabled())
        # 測定中のトレース1を隠すと縦軸は固定のトレース2の単位（dBm）になり、トレース2を描く
        self.cmd("set_trace", number=1, visible=False)
        self.wait(lambda: self.win.display_unit() == "dBm", what="縦軸が dBm")
        self.wait(lambda: self.win.traces.live_payload(2) is not None, what="トレース2を描く")
        self.assertTrue(r2.visible.isEnabled())
        self.assertTrue(self.win.trace_tab.rows[1].visible.isEnabled())  # 測定中のトレースのチェックは無効にしない
        self.assertTrue(self.win.f_ref.isEnabled())
        self.assertEqual(self.win.div_field.name_label.text(), "dB/div")

    def test_marker_reads_swr_and_threshold_field(self):
        self.s11_ready()
        self.reference()
        self.cmd("set_view", s11_format="swr")
        r = self.cmd("marker_peak", polarity="dip", trace=1)
        self.win.select_marker(r["id"])
        self.pump(0.3)
        cells = [self.win.marker_panel.tree.topLevelItem(0).text(i) for i in range(4)]
        self.assertTrue(cells[3].endswith(" SWR"), cells)
        # しきい値の欄は SWR の値の一覧（符号なし・小数2桁）
        th = self.win.marker_tab.th_level
        self.assertEqual(th.choices()[:3], ["1.2 SWR", "1.5 SWR", "2 SWR"])
        self.assertEqual(th.edit.text(), "2.00 SWR")

    def test_axis_view_and_ticks(self):
        st = {"traces": [{"number": 1, "visible": True, "has_values": True, "unit": "SWR"}],
              "amplitude": {"swr_per_div": 0.05}}
        a = screen_s11.axis_view(st, screen_s11.axis_unit(st))
        self.assertTrue(a["linear"])
        self.assertAlmostEqual(a["top"], 1.5)
        self.assertEqual(a["decimals"], 2)
        self.assertEqual(screen_s11.tick_text(1.45, 2), "1.45")
        self.assertEqual(screen_s11.tick_text(-0.0, 1), "0.0")

    def test_axis_unit_follows_live_traces_first(self):
        """縦軸の単位は測定中のトレースで決まる（番号の小さい固定のトレースより先: 2026-10-09 ユーザー決定）。単位の違う
        固定のトレースだけが描かれない。測定中のトレースが表示されていなければ固定のトレースの単位。"""
        fixed = {"number": 1, "action": "fixed", "visible": True, "has_values": True, "unit": "dBm"}
        live = {"number": 2, "action": "update", "visible": True, "has_values": True, "unit": "SWR"}
        st = {"traces": [fixed, live]}
        self.assertEqual(screen_s11.axis_unit(st), "SWR")
        self.assertIn("SWR", screen_s11.trace_drawn_reason(fixed, "SWR"))
        self.assertIsNone(screen_s11.trace_drawn_reason(live, "SWR"))
        st = {"traces": [fixed, {**live, "visible": False}]}
        self.assertEqual(screen_s11.axis_unit(st), "dBm")


if __name__ == "__main__":
    unittest.main()
