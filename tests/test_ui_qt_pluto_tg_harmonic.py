"""Qt 画面の PlutoSDR の TG 高調波の平均除去（設計Q §7.7）: 信号源のタブの送信（PlutoSDR）の欄の選択。

PlutoSDR に繋いでいるときだけ出る（送信の欄ごと）・TG のときだけ選べる（ほかは理由付きで無効）・選択肢は「なし」「4回」
「8回」で、TG のときは名前の後ろにいまの条件での1回の掃引の見積もり・選ぶと命令が出る・幅は秒の桁で動かない。
"""
import unittest

from namibench.core.devices.plutosdr.fake import FAKE_URI
from namibench.ui_qt import screen_pluto, texts
from namibench.ui_qt.tab_traces import combo_width
from namibench.ui_qt.texts_pluto import PLUTO_LABELS, TG_HARMONIC_CHOICES
from tests.test_ui_qt_plutosdr import PlutoUiBase

MHZ = 1_000_000


class TestTgHarmonicSelector(PlutoUiBase):
    def combo(self):
        return self.win.source_tab.pluto_tx.tg_harmonic

    def items(self):
        c = self.combo()
        return [(c.itemText(i), c.itemData(i)) for i in range(c.count())]

    def test_hidden_until_pluto_and_disabled_outside_tg(self):
        tx = self.win.source_tab.pluto_tx
        self.assertTrue(tx.isHidden())  # 未接続: 送信の欄ごと出さない
        self.cmd("connect", backend="simulator", serial="SIM00001")
        self.pump(0.3)
        self.assertTrue(tx.isHidden())  # GigaSt: 出さない
        self.connect_pluto(single=False)
        self.wait(lambda: not tx.isHidden(), what="送信の欄")
        c = self.combo()
        # SA: 選べず、理由はツールチップ。選択肢は名前だけ（見積もりは TG のときだけ）
        self.wait(lambda: not c.isEnabled(), what="TG でないので使えない")
        self.assertIn(PLUTO_LABELS["tg_harmonic_not_tg"], c.toolTip())
        self.assertIn(PLUTO_LABELS["tg_harmonic_tip"], c.toolTip())
        self.assertEqual(self.items(), [(n, int(k)) for k, n in TG_HARMONIC_CHOICES.items()])
        self.assertEqual(c.currentText(), "なし")
        self.cmd("set_mode", mode="sg", sg_hz=85 * MHZ)
        self.pump(0.3)
        self.assertFalse(c.isEnabled())

    def test_tg_shows_estimates_and_sends_the_choice(self):
        self.connect_pluto(single=False)
        self.cmd("set_sweep", start_hz=985 * MHZ, stop_hz=1015 * MHZ)
        self.cmd("set_mode", mode="tg")
        c = self.combo()
        self.wait(lambda: c.isEnabled(), what="TG で使える")
        st = self.state()
        est = st["plutosdr"]["tg_harmonic_estimates"]
        want = [(texts.w("image_reject_item", name=n, s=texts.seconds_text(est[k])), int(k))
                for k, n in TG_HARMONIC_CHOICES.items()]
        self.wait(lambda: self.items() == want, what="名前と見積もり")
        self.assertEqual(c.currentData(), 1)
        self.assertEqual(c.toolTip(), PLUTO_LABELS["tg_harmonic_tip"])
        # 選ぶ（利用者の操作だけが命令を出す: activated）
        i = c.findData(8)
        c.setCurrentIndex(i)
        c.activated.emit(i)
        self.wait(lambda: self.state()["plutosdr"]["tg_harmonic_avg"] == 8, what="8回を選んだ")
        self.wait(lambda: abs(self.state()["sweep"]["estimated_sweep_s"] - est["8"]) < 1e-9, what="見積もりの欄")
        self.wait(lambda: self.win.trace_tab.est_label.text() == texts.w("estimate_value", s=texts.seconds_text(est["8"])),
                  what="掃引時間の見積もりの欄が延びる")
        # コアから変わったら欄も映す
        self.cmd("plutosdr_set", tg_harmonic_avg=4)
        self.wait(lambda: c.currentData() == 4, what="欄が映す")

    def test_width_does_not_follow_seconds(self):
        self.connect_pluto(single=False)
        c = self.combo()
        w = combo_width(c, screen_pluto.tg_harmonic_width_samples())
        self.wait(lambda: c.width() == w, what="見本で決めた幅")
        self.cmd("set_sweep", start_hz=985 * MHZ, stop_hz=1015 * MHZ)
        self.cmd("set_mode", mode="tg")
        self.wait(lambda: c.isEnabled(), what="TG")
        self.pump(0.3)
        self.assertEqual(c.width(), w)

    def test_combo_shows_the_shared_view(self):
        # 欄の中身は Web と同じ関数（screen_pluto.tg_harmonic_view）から作る
        self.connect_pluto(single=False)
        self.cmd("set_mode", mode="tg")
        self.pump(0.3)
        v = screen_pluto.tg_harmonic_view(self.state())
        self.assertEqual(self.items(), [(it["text"], it["code"]) for it in v["items"]])
        self.assertIsNone(v["reason"])


class TestTgHarmonicViewNotPluto(unittest.TestCase):
    def test_reason_when_not_pluto(self):
        v = screen_pluto.tg_harmonic_view({"plutosdr": {"connected": False}, "sweep": {"mode": "tg"}})
        self.assertEqual(v["reason"], PLUTO_LABELS["not_pluto"])
        self.assertEqual([it["text"] for it in v["items"]], list(TG_HARMONIC_CHOICES.values()))
        self.assertEqual(v["value"], 1)


if __name__ == "__main__":
    unittest.main()
