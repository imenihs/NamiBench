"""Qt: 基準と PlutoSDR の受信の利得の補正の表が違うときの理由（設計Q §7.6）。

RBW・イメージ除去の違う基準と同じ場所（状態バーの基準の項目・信号源のタブの基準の文・上段の基準の文のツールチップ・
電力に戻ったときの知らせ）に、語の表（texts.NORM_REASONS）の理由が出る。
"""
from __future__ import annotations

from pygigast.ui_qt import texts
from tests.test_ref_gain_table import A, table
from tests.test_ui_qt_plutosdr import PlutoUiBase

REASON = texts.NORM_REASONS["gain_correction_mismatch"]


class RefGainTableUiTest(PlutoUiBase):
    def sweep_once(self):
        n0 = self.state()["run"]["sweep_count"]
        self.cmd("run", mode="single")
        self.wait(lambda: self.state()["run"]["state"] == "stopped" and self.state()["run"]["sweep_count"] > n0,
                  timeout=20, what="単発の掃引")
        self.pump(0.3)

    def test_reason_shown_where_other_mismatches_are(self):
        self.connect_pluto(single=False)
        self.cmd("set_mode", mode="tg")
        self.cmd("plutosdr_set", tx_atten_db=-10.0)
        self.cmd("reference_take", kind="fine")
        self.wait(lambda: not self.state()["tasks"] and (self.state()["normalization"] or {}).get("selected"),
                  timeout=30, what="基準を取り終える")
        self.sweep_once()
        self.assertTrue(self.state()["normalization"]["applied"])
        self.assertNotIn(REASON, self.win.st_norm.text())
        # 表を保存した後の測定には、表なしで取った基準を当てない
        self.api._inst._pluto_gain_cal = table(A)
        self.sweep_once()
        self.wait(lambda: self.state()["normalization"].get("reason") == "gain_correction_mismatch",
                  what="当てない理由")
        self.wait(lambda: REASON in self.win.st_norm.text(), what="状態バーの基準の項目")
        self.assertIn(REASON, self.win.source_tab.ref_state.text())
        self.assertIn(REASON, self.win.ref_quick.status.toolTip())
        self.wait(lambda: REASON in self.win.status.note.text(), what="電力に戻った知らせ")
        self.assertEqual(texts.normalization_text({"used": True, "applied": False, "reason": "gain_correction_mismatch"}),
                         texts.tpl("norm_none_reason", reason=REASON))
