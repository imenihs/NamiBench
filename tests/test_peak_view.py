"""Qt と Web が同じものを使う画面の中身（設計G §3.4・§3.8・P §9.7）の試験。画面なしで確かめる。

- peak_view: 山／谷の語・メニューの名前・ピークの一覧の欄（基準は表示の量で決める）・結果の文字
- marker_rows.row_ops: 行の「操作」の項目（Qt の行のドロップダウンと Web の行の「操作」が同じ）
- 機能の線: 谷のマーカーの N dB 帯域幅の横線はマーカーの値 + N
- Web の口（view）: マーカー表の行に ops、状態の ui に peaks、ピークの一覧の戻り値に文字
"""
import unittest

from pygigast.api.web import view
from pygigast.ui_qt import marker_rows, peak_view, texts

L = texts.LABELS
MHZ = 1_000_000


class PeakViewTest(unittest.TestCase):
    def test_polarity_words_and_menu(self):
        self.assertEqual(peak_view.polarity_of("dip"), "dip")
        self.assertEqual(peak_view.polarity_of("up"), "peak")
        self.assertEqual(peak_view.polarity_of(None), "peak")
        self.assertEqual(peak_view.other("peak"), "dip")
        for p in peak_view.POLARITIES:
            self.assertEqual(list(texts.SEARCH_BUTTONS[p]), list(peak_view.SEARCH_CODES))
            self.assertEqual(list(texts.SEARCH_TIPS[p]), list(peak_view.SEARCH_CODES))
        self.assertEqual(peak_view.menu_label("marker_peak_lower", "dip"), "次に深い −ピーク")
        self.assertEqual(peak_view.menu_label("marker_peak_lower", "peak"), texts.MENU_ITEMS["marker_peak_lower"])
        self.assertEqual(peak_view.menu_label("marker_clear", "dip"), texts.MENU_ITEMS["marker_clear"])

    def test_list_view_by_quantity(self):
        sa = {"display_quantity": "power"}
        tg = {"display_quantity": "ratio"}
        self.assertEqual(peak_view.list_view(sa, "peak"),
                         {"base": "noise_floor", "label": L["from_floor"], "tip": L["floor_tip"],
                          "diff_header": L["col_height_db"], "reason": None})
        self.assertEqual(peak_view.list_view(sa, "dip")["reason"], texts.REASON_TEXTS["dips_on_power"])
        self.assertEqual(peak_view.list_view({}, "dip")["reason"], texts.REASON_TEXTS["dips_on_power"])  # 掃引の前
        v = peak_view.list_view(tg, "dip")
        self.assertEqual((v["base"], v["label"], v["tip"], v["diff_header"], v["reason"]),
                         ("max", L["from_max"], L["max_tip_dip"], L["col_below_max_db"], None))
        self.assertEqual(peak_view.list_view(tg, "peak")["tip"], L["max_tip_peak"])
        self.assertEqual(set(peak_view.list_views(tg)), {"peak", "dip"})

    def test_result_view(self):
        r = {"trace": 1, "unit": "dB", "base": "max", "base_level": -0.4, "polarity": "dip",
             "peaks": [{"freq_hz": 120_000_000, "level": -30.43, "diff_db": -30.03},
                       {"freq_hz": 170_300_000, "level": -20.0, "diff_db": -19.6}]}
        v = peak_view.result_view(r)
        self.assertEqual(v["found"], "2 個・最大 −0.4 dB")
        self.assertEqual(v["diff_header"], L["col_below_max_db"])
        self.assertEqual(v["rows"][0], {"freq_hz": 120_000_000, "freq": "120", "level": "-30.43 dB", "diff": "-30.0"})
        self.assertEqual(v["rows"][1]["freq"], "170.3")
        # 周波数オフセット（設計O）は表示の周波数に足す。登録に使う周波数（freq_hz）はそのまま
        v = peak_view.result_view(r, 10_000 * MHZ)
        self.assertEqual((v["rows"][0]["freq"], v["rows"][0]["freq_hz"]), ("10120", 120_000_000))
        sa = {"unit": "dBm", "base": "noise_floor", "base_level": -95.24, "peaks": [{"freq_hz": 1, "level": -40.0,
                                                                                     "diff_db": 55.24}]}
        v = peak_view.result_view(sa)
        self.assertEqual((v["found"], v["diff_header"], v["rows"][0]["diff"]),
                         ("1 個・ノイズフロア −95.2 dBm", L["col_height_db"], "55.2"))
        self.assertEqual(peak_view.result_view({"unit": "dB", "base": "max", "base_level": None, "peaks": []})["found"],
                         "0 個")
        # 0 の差は「-0.0」にしない
        v = peak_view.result_view({**r, "peaks": [{"freq_hz": 1, "level": -0.4, "diff_db": -0.01}]})
        self.assertEqual(v["rows"][0]["diff"], "0.0")


class RowOpsTest(unittest.TestCase):
    def snap(self):
        th = {"level": -40.0, "side": "above", "status": "ok", "left_status": "ok", "left_hz": 5, "right_status": "edge",
              "right_hz": None, "width_hz": None, "center_hz": None, "q": None}
        m1 = {"id": "M1", "kind": "M", "trace": 1, "freq_hz": 20, "level": -30.0, "unit": "dBm", "status": "ok",
              "ref": None, "polarity": "dip", "threshold": th,
              "bandwidth": [{"n_db": 3.0, "status": "ok", "left_status": "ok", "left_hz": 10, "right_status": "ok",
                             "right_hz": 30, "width_hz": 20, "center_hz": 20, "q": 1.0}],
              "freq_offsets": [{"id": "M1.f1+", "sign": "+", "offset_hz": 5, "status": "ok", "freq_hz": 25,
                                "level": -40.0, "unit": "dBm", "delta_level_db": -10.0}]}
        m2 = {**m1, "id": "M2", "ref": "M1", "polarity": "peak", "threshold": None, "bandwidth": [], "freq_offsets": []}
        return {"markers": [m1, m2], "xy": {"x1_hz": 100}}

    def test_items(self):
        snap = self.snap()
        rows = {r["id"]: r for r in marker_rows.rows_from_snapshot(snap)}
        ops = marker_rows.row_ops(rows["M1"], snap)
        self.assertEqual([None if o is None else o["text"] for o in ops],
                         [L["row_center"], L["row_ref"], texts.MENU_ITEMS["marker_span"], None, "M1 を消す"])
        self.assertEqual(ops[0]["cmd"], "marker_to")
        self.assertEqual(ops[0]["args"], {"id": "M1", "target": "center"})
        self.assertEqual(ops[2]["why"], texts.REASON_TEXTS["delta_ref"])  # デルタの基準が無い
        self.assertIsNone(marker_rows.row_ops(rows["M2"], snap)[2]["why"])
        self.assertEqual(ops[4]["cmd"], "marker_remove")
        # 子の行・XY カーソル: その行だけを消す（remove_command と同じ命令）
        for rid, text in (("M1.f1+", L["row_remove_cursor"]), ("M1.th", L["row_remove_function"]),
                          ("X1", "X1 を外す")):
            ops = marker_rows.row_ops(rows[rid], snap)
            rm = marker_rows.remove_command(rows[rid], snap)
            self.assertEqual([(o["text"], o["cmd"], o["args"]) for o in ops], [(text, rm[0], rm[1])], rid)

    def test_dip_marker_note_and_bandwidth_line(self):
        snap = self.snap()
        rows = {r["id"]: r for r in marker_rows.rows_from_snapshot(snap)}
        self.assertIn(L["row_dip"], rows["M1"]["note"])
        self.assertNotIn(L["row_dip"], rows["M2"]["note"])
        # 谷のマーカーの N dB 帯域幅の線は谷から N 上がったレベル（G §3.8）
        self.assertEqual(rows["M1.bw3"]["line"], {"level": -27.0, "freqs_hz": [10, 30]})

    def test_web_view(self):
        snap = self.snap()
        mv = view.markers_view(snap)
        ops = {r["id"]: r["ops"] for r in mv["rows"]}
        self.assertEqual(ops["M1"], marker_rows.row_ops(next(r for r in marker_rows.rows_from_snapshot(snap)
                                                             if r["id"] == "M1"), snap))
        st = view.state_view({"sweep": {}, "run": {}, "connection": {}, "display_quantity": "ratio", "traces": []})
        self.assertEqual(st["ui"]["peaks"]["dip"]["label"], L["from_max"])
        r = view.result_view("peak_list", {}, {"trace": 1, "unit": "dB", "base": "max", "base_level": 0.0,
                                               "polarity": "dip", "peaks": []})
        self.assertEqual(r["texts"]["peak_list"]["found"], "0 個・最大 0.0 dB")


if __name__ == "__main__":
    unittest.main()
