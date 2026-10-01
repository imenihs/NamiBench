"""Qt 画面の RTL-SDR の欄（設計Q §6.4〜§6.8）: 偽物の DLL の RTL-SDR に繋いで確かめる。

受信の利得・ppm・個体の校正の2つのボタン・単位の表示・FFT の機器で使えない欄・RBW の窓ごとのまとまり・
聞く（イベント audio を鳴らす部品へ渡す）・rtl_tcp の設定と状態バー。
"""
import unittest

from pygigast.api.local import LocalApi, _RtlSdrBackend, _SimulatorBackend
from pygigast.core.devices.base import load_profile
from pygigast.core.instrument import GIGAST_PROFILE_PATH, Instrument
from pygigast.ui_qt import texts
from tests.test_instrument_rtlsdr import FakeLib, free_port
from tests.test_ui_qt_support import UiBase

MHZ = 1_000_000
UNCAL = "dB（未校正）"


class RtlUiBase(UiBase):
    def setUp(self):
        self.lib = FakeLib()
        self.lib.tones = [(84_700_000, 0.05)]
        super().setUp()

    def make_window(self):
        profile = load_profile(GIGAST_PROFILE_PATH)
        sim = _SimulatorBackend(profile)
        sim.speed = 0.05
        rtl = _RtlSdrBackend(lib_factory=lambda: self.lib)
        self.api.close()
        self.api = LocalApi(Instrument({"simulator": sim, "rtlsdr": rtl}, profile=profile,
                                       data_root=self.root / "d", cal_root=self.root / "c"))
        return super().make_window()

    def connect_rtl(self, single=True):
        self.cmd("connect", backend="rtlsdr", port="rtlsdr:0")
        self.cmd("set_sweep", start_hz=84 * MHZ, stop_hz=85_500_000)
        if single:
            n0 = self.state()["run"]["sweep_count"]
            self.cmd("run", mode="single")
            self.wait(lambda: self.state()["run"]["state"] == "stopped" and self.state()["run"]["sweep_count"] > n0,
                      what="単発の掃引")
            self.wait(lambda: self.win.traces.live_payload(1) is not None, what="トレース1")
        self.pump(0.3)

    def add_marker(self, hz=84_700_000):
        r = self.cmd("marker_add", kind="M", trace=1, freq_hz=hz)
        self.win.select_marker(r["id"])
        self.wait(lambda: any(m["id"] == r["id"] for m in self.win.marker_snap["markers"]), what="マーカー")
        self.pump(0.2)
        return r["id"]


class TestRtlControls(RtlUiBase):
    def sections(self):
        """「トレース・掃引」のタブの欄の並び（出ているものだけ）。"""
        tab = self.win.trace_tab
        names = {id(tab.g_sweep): "sweep", id(tab.g_traces): "traces", id(tab.g_files): "files",
                 id(self.win.rtl_panel): "rtlsdr"}
        lay = tab._body
        out = []
        for i in range(lay.count()):
            w = lay.itemAt(i).widget()
            if w is not None and id(w) in names and not w.isHidden():
                out.append(names[id(w)])
        return out

    def test_shown_only_while_rtlsdr_connected_then_controls_follow_device(self):
        # 機器に固有の欄は、その機器に繋いでいるときだけ出す（設計J §4.5）。掃引が一番上
        p = self.win.rtl_panel
        self.assertTrue(p.isHidden())
        self.assertEqual(self.sections(), ["sweep", "traces", "files"])
        # GigaSt（シミュレータ）に繋いでも出さない
        self.cmd("connect", backend="simulator", serial="SIM00001")
        self.pump(0.3)
        self.assertTrue(p.isHidden())
        self.connect_rtl()
        self.assertEqual(self.sections(), ["sweep", "traces", "files", "rtlsdr"])
        self.assertTrue(p.gain.isEnabled())
        # 段（29）と AGC
        self.assertEqual(p.gain.count(), 30)
        self.assertEqual(p.gain.itemText(0), texts.TEMPLATES["gain_agc"])
        self.assertEqual(p.gain.currentData(), 29.7)
        self.assertIn(UNCAL, p.unit_text.text())
        # 切ったら消える
        self.cmd("disconnect")
        self.wait(lambda: p.isHidden(), what="受信の欄が消える")

    def test_gain_and_agc_go_to_core_and_level_needs_manual_gain(self):
        self.connect_rtl()
        p = self.win.rtl_panel
        self.add_marker()
        i = p.gain.findData(40.2)
        p.gain.setCurrentIndex(i)
        p.gain.activated.emit(i)
        self.wait(lambda: self.state()["rtlsdr"]["gain_db"] == 40.2, what="利得")
        self.assertTrue(p.level_btn.isEnabled())
        p.gain.setCurrentIndex(0)
        p.gain.activated.emit(0)
        self.wait(lambda: self.state()["rtlsdr"]["agc"], what="AGC")
        self.wait(lambda: not p.level_btn.isEnabled(), what="レベルの校正が無効")
        self.assertIn(texts.RTL_LABELS["needs_manual_gain"], p.level_btn.toolTip())

    def test_ppm_and_level_calibration_from_marker(self):
        self.connect_rtl()
        p = self.win.rtl_panel
        mid = self.add_marker()
        self.assertEqual(p.marker.currentData(), mid)
        # マーカーは 84.7MHz に見えた信号。本当は 84.705MHz だった → ppm は約 +59
        p.true_freq.set_hz(84_705_000)
        p.ppm_btn.click()
        self.wait(lambda: p.result.isVisible(), what="ppm の結果")
        self.assertIn("ppm を", p.result.text())
        self.wait(lambda: self.state()["rtlsdr"]["ppm"] == 59, what="ppm が保存される")
        self.wait(lambda: p.ppm.value() == 59, what="ppm の欄")
        self.wait(lambda: "+59" in p.cal_text.text(), what="個体の校正の日時と ppm")
        # レベル: 単位が dBm に変わる
        self.connect_rtl()
        self.add_marker()
        p.true_dbm.set_value(-40.0)
        p.level_btn.click()
        self.wait(lambda: "レベルの補正" in p.result.text(), what="レベルの結果")
        self.connect_rtl()
        self.wait(lambda: self.win.display_unit() == "dBm", what="単位 dBm")
        self.assertIn("dBm", p.unit_text.text())


class TestFftDeviceFields(RtlUiBase):
    def test_rbw_grouped_by_window_and_fft_reasons(self):
        self.connect_rtl()
        c = self.win.rbw_combo
        items = [(c.itemText(i), c.itemData(i)) for i in range(c.count())]
        self.assertEqual(items[0], ("Hann", None))
        heads = [t for t, d in items if d is None]
        self.assertEqual(heads, ["Hann", "Flat top"])
        self.assertEqual(c.currentData(), 1500)
        # 見出しは選べない
        self.assertFalse(c.model().item(0).isEnabled())
        tab = self.win.trace_tab
        self.assertFalse(tab.points_auto.isEnabled())
        self.assertIn(texts.RTL_LABELS["points_fft"], tab.points_auto.toolTip())
        self.assertFalse(tab.image_cancel.isEnabled())
        self.assertIn(texts.RTL_LABELS["zero_span_fft"], tab.sweep_note.text())
        # 受信だけの機器は TG・SG を測れない
        self.assertFalse(self.win.mode_buttons["tg"].isEnabled())
        self.assertIn(texts.MODE_UNSUPPORTED, self.win.mode_buttons["tg"].toolTip())
        self.assertTrue(self.win.mode_buttons["sa"].isEnabled())
        # 縦軸・REF の単位
        self.assertEqual(self.win.display_unit(), UNCAL)
        self.assertIn(UNCAL, self.win.f_ref.edit.text())
        # 選ぶと好みの RBW が変わる
        i = c.findData(471)
        c.setCurrentIndex(i)
        c.activated.emit(i)
        self.wait(lambda: self.state()["sweep"]["rbw_preference_hz"] == 471, what="Flat top の RBW")

    def test_hann_note_in_marker_table(self):
        self.connect_rtl()
        self.add_marker()
        self.wait(lambda: any(texts.MARKER_NOTES["hann_scalloping"] in (r.get("note") or "")
                              for r in self.win.marker_row_list), what="Hann の注意")
        row = next(r for r in self.win.marker_row_list if r["id"] == "M1")
        self.assertEqual(row["unit"], UNCAL)

    def test_gigast_keeps_its_fields(self):
        self.connect_and_sweep()
        c = self.win.rbw_combo
        self.assertEqual([c.itemData(i) for i in range(c.count())], [180000, 30000, 15000])
        self.assertTrue(self.win.trace_tab.points_auto.isEnabled())
        self.assertTrue(self.win.mode_buttons["tg"].isEnabled())
        self.assertFalse(self.win.trace_tab.sweep_note.isVisible())


class TestListen(RtlUiBase):
    def test_listen_from_menu_status_and_audio(self):
        played = []
        self.win.listen.player.play = lambda samples, rate: played.append((len(samples), rate))
        a = self.win.actions["marker_listen"]
        self.assertFalse(a.isEnabled())
        self.connect_rtl()
        self.assertFalse(a.isEnabled())  # マーカーを選んでいない
        self.assertIn(texts.REASON_TEXTS["select_marker"], a.toolTip())
        self.add_marker()
        self.assertTrue(a.isEnabled())
        i = self.win.listen.mode.findData("fm_broadcast")
        self.win.listen.mode.setCurrentIndex(i)
        a.trigger()
        self.wait(lambda: self.state()["listen"]["active"], what="聞いている")
        self.wait(lambda: self.win.st_listen.isVisible() and "FM放送" in self.win.st_listen.text(), what="状態バー")
        self.assertTrue(self.win.status.listen_stop.isVisible())
        self.assertIn("M1", self.win.listen.now.text())
        self.wait(lambda: played, what="音が鳴らす部品へ届く")
        self.assertEqual(played[0][1], 48000)
        # 右クリックのメニューにも「この位置を聞く」
        menu = self.win._context_menu(84_700_000, -50)
        names = [x.objectName() for x in menu.actions()]
        self.assertIn("ctx_m_listen", names)
        # 状態バーの「やめる」
        self.win.status.listen_stop.click()
        self.wait(lambda: not self.state()["listen"]["active"], what="やめた")
        self.wait(lambda: not self.win.st_listen.isVisible(), what="状態バーから消える")
        self.wait(lambda: "聞くのをやめました" in self.win.st_note.text(), what="やめた知らせ")

    def test_listen_reason_on_gigast(self):
        self.connect_and_sweep()
        r = self.cmd("marker_add", kind="M", trace=1)
        self.win.select_marker(r["id"])
        self.pump(0.2)
        self.assertFalse(self.win.listen.start_btn.isEnabled())
        self.assertIn(texts.LISTEN_LABELS["not_iq"], self.win.listen.start_btn.toolTip())


class TestRtlTcpSettings(RtlUiBase):
    def test_settings_dialog_and_status_stop(self):
        port = free_port()
        self.win.actions["settings"].trigger()
        self.pump(0.2)
        dlg = self.win._settings
        self.assertIsNotNone(dlg)
        dlg.port.setValue(port)
        self.wait(lambda: self.state()["rtltcp"]["port"] == port, what="ポート")
        dlg.enable.setChecked(True)
        self.wait(lambda: self.state()["rtltcp"]["running"], what="送出中")
        self.wait(lambda: self.win.st_rtltcp.isVisible() and f":{port}" in self.win.st_rtltcp.text(),
                  what="状態バーの rtl_tcp")
        self.assertTrue(self.win.status.rtltcp_stop.isVisible())
        self.wait(lambda: "送出中" in dlg.rt_state.text(), what="設定の画面の状態")
        self.win.status.rtltcp_stop.click()
        self.wait(lambda: not self.state()["rtltcp"]["running"], what="止まる")
        self.wait(lambda: not self.win.st_rtltcp.isVisible(), what="状態バーから消える")
        self.wait(lambda: not dlg.enable.isChecked(), what="設定の画面も止めた状態")
        dlg.close()


if __name__ == "__main__":
    unittest.main()
