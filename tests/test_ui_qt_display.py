"""Qt の設計P の表示（ウォーターフォール・密度・再生・バンドプラン・ピークの一覧から Center）を確かめる。"""
from __future__ import annotations

import time

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QPushButton

from pygigast.ui_qt import texts
from pygigast.ui_qt.waterfall_view import ROW_PX
from tests.test_ui_qt_support import UiBase

MHZ = 1_000_000
D = texts.DISPLAY_TEXTS


class WaterfallUiTest(UiBase):
    def sweep_n(self, n):
        for _ in range(n):
            n0 = self.state()["run"]["sweep_count"]
            self.cmd("run", mode="single")
            self.wait(lambda: self.state()["run"]["state"] == "stopped" and self.state()["run"]["sweep_count"] > n0,
                      what="単発")

    def test_layout_rows_follow_scroll_and_pick(self):
        self.cmd("connect", backend="simulator", serial="SIM00001")
        self.cmd("set_sweep", start_hz=50 * MHZ, stop_hz=200 * MHZ)
        self.cmd("set_view", layout="waterfall")
        wf = self.win.display.waterfall
        self.wait(lambda: self.win.parts.lower["waterfall"].holder.isVisible(), what="ウォーターフォールを出す")
        self.sweep_n(3)
        self.wait(lambda: len(wf.shown_rows) == 3, what="1掃引＝1行")
        self.assertTrue(wf.follow)
        # 新しい行が上（P §9.1）
        self.assertGreater(wf.shown_rows[0]["t"], wf.shown_rows[-1]["t"])
        # 横軸はスペクトルと同じ範囲（P §2）
        self.assertEqual((wf.x0, wf.x1), (50 * MHZ, 200 * MHZ))
        self.assertEqual(wf.visible_rows(), int(wf.box.sceneBoundingRect().height() // ROW_PX))
        # 最新に付いている間は「最新へ」を理由付きで使えない（U-08）
        self.assertFalse(wf.follow_btn.isEnabled())
        self.assertEqual(wf.follow_btn.toolTip(), texts.REASON_TEXTS["wf_following"])
        # 時刻の軸の上のホイール → 前へずらす（付いていくのをやめる）・「最新へ」で戻る
        wf.sig.time_scroll.emit(-1)
        self.pump(0.3)
        self.assertFalse(self.win.display.follow)
        self.assertTrue(wf.follow_btn.isEnabled())
        wf.follow_btn.click()
        self.pump(0.3)
        self.assertTrue(self.win.display.follow)
        # 時刻の範囲を選ぶ（時間方向の拡大: P §5）
        rows = list(wf.shown_rows)
        wf.sig.time_range.emit(rows[-1]["t"], rows[0]["t"])
        self.wait(lambda: not self.win.display.follow and len(wf.shown_rows) >= 1, what="時間の範囲")
        wf.follow_btn.click()
        self.wait(lambda: self.win.display.follow and len(wf.shown_rows) == 3, what="最新へ")
        # 行をクリック → 過去のトレース（P §3）
        wf.click_at((wf.x0 + wf.x1) / 2, 0.5)
        self.wait(lambda: any(f.get("kind") == "past" for f in self.state()["files"]), what="過去のトレース")
        self.wait(lambda: "W.1" in self.win.traces.files, what="過去のトレースを描く")
        self.assertEqual(wf.cursor_t, wf.shown_rows[0]["t"])  # どの行を出しているかに線
        # 行の上の読み値: 時刻・周波数・レベル（P §3）
        r = wf.shown_rows[0]
        lines = self.win.display._reading(r, r["start_hz"] + r["step_hz"] * 3, -50.0)
        self.assertEqual(len(lines), 3)
        self.assertIn("MHz", lines[1])
        self.assertIn("dBm", lines[2])

    def test_density_and_display_tab(self):
        self.cmd("connect", backend="simulator", serial="SIM00001")
        self.cmd("set_sweep", start_hz=50 * MHZ, stop_hz=200 * MHZ)
        g = self.win.display_tab.wf_group
        self.win.display_tab.view_density.click()
        self.wait(lambda: self.win.parts.lower["density"].holder.isVisible(), what="密度表示を出す")
        self.sweep_n(2)
        dv = self.win.display.density
        self.wait(lambda: dv._last is not None and dv._last.get("image") is not None, what="密度の絵")
        self.wait(lambda: bool(dv.seconds.text()), what="何秒分の密度か")
        self.assertTrue(dv.seconds.text().startswith("約"))
        # 色の範囲が自動の間は下限・上限を理由付きで使えない（U-08）
        self.assertTrue(g.auto.isChecked())
        self.assertFalse(g.lo.edit.isEnabled())
        self.assertEqual(g.lo.edit.toolTip(), texts.REASON_TEXTS["wf_auto_on"])
        g.auto.click()
        self.wait(lambda: not self.state()["view"]["wf_color_auto"], what="自動を切る")
        self.wait(lambda: g.lo.edit.isEnabled(), what="下限を打てる")
        # カラーマップは配色の一部（J §4.4）
        i = g.colormap.findData("magma")
        g.colormap.setCurrentIndex(i)
        g.colormap.activated.emit(i)
        self.wait(lambda: self.state()["palette"]["colormap"] == "magma", what="カラーマップ")
        self.wait(lambda: dv.colormap == "magma" and self.win.display.waterfall.colormap == "magma",
                  what="両方に当たる")
        i = g.half.findData(50)
        g.half.setCurrentIndex(i)
        g.half.activated.emit(i)
        self.wait(lambda: self.state()["view"]["density_half"] == 50, what="半分になる回数")

    def test_zero_span_hides_lower_parts_with_reason(self):
        # ゼロスパン（横軸が時間: 設計N §1）の間はウォーターフォール・密度を出さない。設定は変えず、選べない理由を出す
        self.cmd("connect", backend="simulator", serial="SIM00001")
        self.cmd("set_sweep", start_hz=50 * MHZ, stop_hz=200 * MHZ)
        self.cmd("set_view", layout="waterfall")
        holder = self.win.parts.lower["waterfall"].holder
        self.wait(lambda: holder.isVisible(), what="ウォーターフォールを出す")
        self.cmd("set_sweep", center_hz=100 * MHZ, span_hz=0)
        self.wait(lambda: not holder.isVisible(), what="ゼロスパンで隠す")
        why = texts.REASON_TEXTS["layout_zero_span"]
        tab = self.win.display_tab
        self.assertEqual(self.state()["view"]["layout"], "waterfall")  # 設定は変えない
        self.assertTrue(tab.view_waterfall.isChecked())
        self.assertFalse(tab.view_waterfall.isEnabled())
        self.assertIn(why, tab.view_waterfall.toolTip())
        self.assertFalse(tab.view_density.isEnabled())
        act = self.win.actions["view_density"]
        self.assertFalse(act.isEnabled())
        self.assertIn(why, act.toolTip())
        # 抜けたら元の組み合わせに戻る
        self.cmd("set_sweep", start_hz=50 * MHZ, stop_hz=200 * MHZ)
        self.wait(lambda: holder.isVisible(), what="ゼロスパンを抜けたら出す")
        self.assertTrue(tab.view_density.isEnabled())

    def test_menu_toggles_layout_and_checks(self):
        act = self.win.actions["view_waterfall"]
        self.assertTrue(act.isEnabled())
        act.trigger()
        self.wait(lambda: self.state()["view"]["layout"] == "waterfall", what="メニューで＋ウォーターフォール")
        self.wait(lambda: act.isChecked(), what="印")
        act.trigger()
        self.wait(lambda: self.state()["view"]["layout"] == "spectrum", what="もう一度でスペクトルのみ")
        self.wait(lambda: not act.isChecked(), what="印を外す")


class BandplanUiTest(UiBase):
    def test_bookmarks_add_center_and_marks(self):
        self.cmd("connect", backend="simulator", serial="SIM00001")
        self.cmd("set_sweep", start_hz=50 * MHZ, stop_hz=200 * MHZ)
        g = self.win.display_tab.bandplan_group
        self.win.show_part("tab-view")
        g.name.setText("ビーコン")
        g.freq.setText("120 MHz")
        g.add_btn.click()
        self.wait(lambda: len(self.state()["bandplan"]["bookmarks"]) == 1, what="ブックマークを足す")
        self.wait(lambda: g.list_box.isVisible(), what="一覧")
        self.assertFalse(g.empty.isVisible())
        center = [b for b in g.list_box.findChildren(QPushButton) if b.text() == D["to_center"]][0]
        center.click()
        self.wait(lambda: self.state()["sweep"]["center_hz"] == 120 * MHZ, what="Center に")
        # グラフの下の端の印をクリック → Center（P §6）
        self.cmd("set_sweep", start_hz=50 * MHZ, stop_hz=200 * MHZ)
        self.win.grab()  # 描くと印の当たりができる
        hits = self.win.display.bands._hits
        self.assertEqual(len(hits), 1)
        self.assertEqual(self.win.display.bands.bookmark_at(hits[0][0].center()), 120 * MHZ)
        self.assertIsNone(self.win.display.bands.bookmark_at(QPointF(-100, -100)))
        # 名前が空なら足さずに欄に理由（ブックマークの名前）
        g.name.setText("")
        g.add_btn.click()
        self.pump(0.2)
        self.assertEqual(len(self.state()["bandplan"]["bookmarks"]), 1)
        # 右クリックの「ここをブックマークに…」（名前を聞く）
        self.text_answer = "B"
        m = self.win._context_menu(100 * MHZ, -50.0)
        act = next(a for a in m.actions() if a.objectName() == "ctx_bookmark")
        act.trigger()
        self.wait(lambda: len(self.state()["bandplan"]["bookmarks"]) == 2, what="右クリックで足す")
        # 消す
        rm = [b for b in g.list_box.findChildren(QPushButton) if b.text() == texts.LABELS["remove"]]
        self.wait(lambda: len([b for b in g.list_box.findChildren(QPushButton)
                               if b.text() == texts.LABELS["remove"]]) == 2, what="一覧に2行")
        rm = [b for b in g.list_box.findChildren(QPushButton) if b.text() == texts.LABELS["remove"]]
        rm[0].click()
        self.wait(lambda: len(self.state()["bandplan"]["bookmarks"]) == 1, what="消す")

    def test_peak_row_center(self):
        self.connect_and_sweep()
        tab = self.win.marker_tab
        tab.pk_find.click()
        self.wait(lambda: tab.pk_table.rowCount() > 0, what="ピークの一覧")
        cell = tab.pk_table.cellWidget(0, 3)  # 操作の列（その後ろは内部の漏れの印の列: 設計L §1.4）
        bs = cell.findChildren(QPushButton)
        self.assertEqual([b.text() for b in bs], [texts.LABELS["register_p"], D["to_center"]])
        freq = float(tab.pk_table.item(0, 0).text()) * 1e6
        bs[1].click()
        self.wait(lambda: abs(self.state()["sweep"]["center_hz"] - freq) < 1e6, what="一覧の行から Center に")


class PlaybackUiTest(UiBase):
    def test_playback_bar_live_indicator_and_back(self):
        self.cmd("connect", backend="simulator", serial="SIM00001")
        self.cmd("set_sweep", start_hz=55 * MHZ, stop_hz=200 * MHZ)
        self.cmd("set_sweep", start_hz=50 * MHZ, stop_hz=200 * MHZ)  # 元に戻す列に条件の変更を積む
        self.cmd("record_start")
        for _ in range(5):
            n0 = self.state()["run"]["sweep_count"]
            self.cmd("run", mode="single")
            self.wait(lambda: self.state()["run"]["state"] == "stopped" and self.state()["run"]["sweep_count"] > n0,
                      what="単発")
        end = time.monotonic() + 10
        while self.state()["recording"].get("sweeps", 0) < 5 and time.monotonic() < end:
            self.pump(0.1)
        self.cmd("record_stop")
        h5 = self.root / "rec.h5"
        self.save_path = str(h5)
        self.win.files.record_finish()
        self.wait(lambda: h5.exists(), what="記録をまとめる")
        self.assertEqual(self.win.status.labels["view"].text(), texts.DISPLAY_TEXTS["status_live"])
        ext = self.win.actions["playback_extract"]
        self.assertFalse(ext.isEnabled())  # 再生していなければ切り出せない（理由付き）
        self.assertIn(texts.REASON_TEXTS["not_playing"], ext.toolTip())
        self.open_paths = [str(h5)]
        self.win.files.open_files()
        bar = self.win.display.bar
        self.wait(lambda: bar.isVisible(), what="再生の帯")
        self.wait(lambda: self.state()["playback"]["active"], what="再生")
        st = self.state()
        self.assertEqual(st["view"]["layout"], "waterfall")
        # ライブか再生かを状態バーに（P §5）
        self.wait(lambda: self.win.status.labels["view"].text().startswith("再生"), what="状態バーの再生")
        # 再生中は連続・単発・モードを理由付きで使えない（P §9.4）
        self.assertFalse(self.win.btn_cont.isEnabled())
        self.assertIn(texts.REASON_TEXTS["playback_active"], self.win.btn_cont.toolTip())
        self.assertFalse(self.win.mode_buttons["tg"].isEnabled())
        # ライブの条件を変える欄・操作も理由付きで使えない（RBW・Span 固定・SG・元に戻す: 設計P §5）
        why = texts.REASON_TEXTS["playback_active"]
        for w in (self.win.rbw_combo, self.win.span_lock, self.win.source_tab.f_sg):
            self.assertFalse(w.isEnabled(), w)
            self.assertIn(why, w.toolTip())
        undo = self.win.actions["undo"]  # 前に Start・Stop を変えた（次に戻す変更は条件）
        self.assertFalse(undo.isEnabled())
        self.assertIn(why, undo.toolTip())
        # 上段の欄は再生の表示の範囲（記録の範囲）
        self.assertEqual(self.win.f_start.hz(), st["playback"]["view"]["start_hz"])
        self.assertEqual(bar.slider.maximum(), st["playback"]["total"] - 1)
        self.assertTrue(bar.resolution.text())
        # つまみ → カーソル
        bar.slider.setValue(3)
        self.wait(lambda: self.state()["playback"]["index"] == 3, what="つまみでカーソル")
        self.wait(lambda: bar.position.text().endswith(f"4 / {st['playback']['total']}"), what="位置の文字")
        # ウォーターフォールは記録の全体（縦いっぱい）
        wf = self.win.display.waterfall
        self.wait(lambda: not wf.follow and len(wf.shown_rows) >= 1, what="記録の全体")
        self.assertEqual(wf.follow_btn.text(), D["whole"])
        # メニューの切り出し → 名前を聞いて記録の全体・いまの表示の周波数で H5 を作る（H §3.1）
        self.wait(lambda: ext.isEnabled(), what="切り出しが使える")
        cut = self.root / "cut.h5"
        self.save_path = str(cut)
        ext.trigger()
        self.wait(lambda: cut.exists() and not self.state()["tasks"], what="切り出し")
        # 時間の範囲を選んだまま別の記録を開く → 新しい記録は全体を出す（記録ごとの見方は捨てる: P §5）
        rows = list(wf.shown_rows)
        wf.sig.time_range.emit(rows[-1]["t"], rows[-1]["t"] + 1e-3)
        self.wait(lambda: self.win.display.window is not None, what="時間の範囲")
        h5b = self.root / "rec2.h5"
        h5b.write_bytes(h5.read_bytes())
        self.open_paths = [str(h5b)]
        self.win.files.open_files()
        self.wait(lambda: self.state()["playback"].get("name") == "rec2.h5", what="別の記録")
        self.wait(lambda: self.win.display.window is None and len(wf.shown_rows) == len(rows), what="新しい記録の全体")
        self.assertFalse(wf.follow_btn.isEnabled())  # 全体を出している（「全体」は理由付きで使えない）
        # 再生・一時停止
        bar.play.click()
        self.wait(lambda: self.state()["playback"]["playing"] or self.state()["playback"]["index"] == 4, what="再生")
        # ライブに戻る
        bar.live.click()
        self.wait(lambda: not self.state()["playback"]["active"], what="ライブに戻る")
        self.wait(lambda: not bar.isVisible(), what="帯を隠す")
        self.wait(lambda: self.win.btn_cont.isEnabled(), what="連続が使える")
