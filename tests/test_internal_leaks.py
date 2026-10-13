"""内部の漏れの印（設計L §1.4・P §9.6）の試験。

- 計算（display_marks）: SG・TG のときだけ、機種のプロファイルの漏れの周波数（固定・SG からのずれ）のうち表示範囲に
  入るもの。TG は固定だけ（SG からのずれは掃引に乗らない）。SA・NF・ゼロスパンでは無い。漏れとみなす幅は RBW＋点の間隔
- b3 で動く漏れ（956MHz は b3=1 で 946MHz: T2）: b3=1 だけを送った掃引（イメージ除去なし・ステップ掃引の間と結果）は
  946MHz、b3 の両方を合わせた掃引（スピード・正確）は消えるので印を付けない。動かない漏れはいつもそのまま
- 本体: 状態の internal_leaks・ピークの一覧の行の leak（シミュレータの 1480MHz と SG+1480MHz）・表示の設定 leak_marks
- 画面の中身（Qt と Web で同じ）: グラフの印（消す設定）・ピークの一覧の列の文字とツールチップ・マウスの位置の読み値
"""
import unittest

import numpy as np

from namibench.core import display_marks as dm
from namibench.core.devices.base import load_profile
from namibench.core.instrument import GIGAST_PROFILE_PATH
from namibench.ui_qt import peak_view, screen_model, source_view, texts
from tests.instrument_helpers import InstrumentCase

MHZ = 1_000_000
PROFILE = load_profile(GIGAST_PROFILE_PATH)


class LeakCalcTest(unittest.TestCase):
    def test_modes(self):
        # b3=1 だけを送った掃引（イメージ除去なし・ステップ掃引）: 956MHz のスプリアスは 946MHz に出る（T2）
        sg = dm.internal_leaks("sg", PROFILE, 500 * MHZ, 0, 3000 * MHZ)
        self.assertEqual([(x["freq_hz"], x["kind"]) for x in sg],
                         [(946 * MHZ, "fixed"), (1480 * MHZ, "fixed"), (1980 * MHZ, "sg_offset")])
        self.assertEqual(sg[2]["offset_hz"], 1480 * MHZ)
        tg = dm.internal_leaks("tg", PROFILE, 500 * MHZ, 0, 3000 * MHZ)
        self.assertEqual([x["freq_hz"] for x in tg], [946 * MHZ, 1480 * MHZ])
        self.assertEqual([x["freq_hz"] for x in dm.internal_leaks("s11", PROFILE, None, 0, 3000 * MHZ)],
                         [946 * MHZ, 1480 * MHZ])
        for mode in ("sa", "nf", None):
            self.assertEqual(dm.internal_leaks(mode, PROFILE, 500 * MHZ, 0, 3000 * MHZ), [])
        self.assertEqual(dm.internal_leaks("sg", PROFILE, 500 * MHZ, 500 * MHZ, 500 * MHZ), [])  # ゼロスパン
        self.assertEqual(dm.internal_leaks("sg", {}, 500 * MHZ, 0, 3000 * MHZ), [])  # 漏れを持たない機種

    def test_b3_positions(self):
        # b3 で動く漏れ（956MHz）は送った b3 で決まる。動かない漏れ（1480MHz・SG + 1480MHz）はいつもそのまま
        self.assertEqual(dm.b3_shifts(PROFILE), {956 * MHZ: -10 * MHZ})
        only_b3_1 = [x["freq_hz"] for x in dm.internal_leaks("sg", PROFILE, 500 * MHZ, 900 * MHZ, 2000 * MHZ,
                                                             b3_both=False)]
        both = [x["freq_hz"] for x in dm.internal_leaks("sg", PROFILE, 500 * MHZ, 900 * MHZ, 2000 * MHZ,
                                                        b3_both=True)]
        self.assertEqual(only_b3_1, [946 * MHZ, 1480 * MHZ, 1980 * MHZ])
        self.assertEqual(both, [1480 * MHZ, 1980 * MHZ])  # 両方の b3 を合わせると動く漏れは消える
        self.assertEqual([x["freq_hz"] for x in dm.fixed_leaks(PROFILE, b3_both=False)], [946 * MHZ, 1480 * MHZ])
        self.assertEqual([x["freq_hz"] for x in dm.fixed_leaks(PROFILE, b3_both=True)], [1480 * MHZ])
        # 動く量を持たないプロファイル（前の形）は今までどおり
        old = {"internal_spurs_hz": {"fixed": [956 * MHZ], "sg_offset": []}}
        self.assertEqual([x["freq_hz"] for x in dm.fixed_leaks(old, b3_both=True)], [956 * MHZ])

    def test_range_edges(self):
        # 表示範囲の端ちょうどは入る・1Hz 外は入らない
        got = dm.internal_leaks("tg", PROFILE, None, 1480 * MHZ, 1500 * MHZ)
        self.assertEqual([x["freq_hz"] for x in got], [1480 * MHZ])
        self.assertEqual(dm.internal_leaks("tg", PROFILE, None, 1480 * MHZ + 1, 1500 * MHZ), [])
        # SG からのずれが固定の周波数と重なっても1つ（SG 0Hz は無いので 1480 と 2960 で確かめる）
        got = dm.internal_leaks("sg", {"internal_spurs_hz": {"fixed": [1480 * MHZ], "sg_offset": [0]}},
                                1480 * MHZ, 0, 3000 * MHZ)
        self.assertEqual(len(got), 1)

    def test_tolerance_and_match(self):
        self.assertEqual(dm.leak_tolerance_hz(180_000, 1_000_000), 1_180_000)
        self.assertEqual(dm.leak_tolerance_hz(15_000, 0), 15_000)
        leaks = dm.internal_leaks("sg", PROFILE, 500 * MHZ, 0, 3000 * MHZ)
        tol = 200_000
        self.assertEqual(dm.leak_at(1480 * MHZ + tol, leaks, tol)["freq_hz"], 1480 * MHZ)
        self.assertIsNone(dm.leak_at(1480 * MHZ + tol + 1, leaks, tol))
        self.assertEqual(dm.leak_at(1980 * MHZ - 5, leaks, tol)["kind"], "sg_offset")
        self.assertIsNone(dm.leak_at(500 * MHZ, leaks, tol))


class LeakViewTest(unittest.TestCase):
    def state(self, **view):
        leaks = dm.internal_leaks("sg", PROFILE, 500 * MHZ, 0, 3000 * MHZ)
        return {"internal_leaks": {"leaks": leaks, "tolerance_hz": 1_180_000}, "view": view}

    def test_marks_and_toggle(self):
        marks = source_view.leak_marks_view(self.state())
        self.assertEqual([m["freq_hz"] for m in marks], [946 * MHZ, 1480 * MHZ, 1980 * MHZ])
        self.assertEqual(marks[1]["tip"], "内部の漏れの可能性 1480 MHz")
        self.assertEqual(marks[2]["tip"], "内部の漏れの可能性 1980 MHz（SG + 1480 MHz）")
        self.assertEqual(marks[0]["near_hz"], 1_180_000)
        self.assertEqual(source_view.leak_marks_view(self.state(leak_marks=False)), [])
        # 周波数オフセット（設計O）: 説明は横軸と同じ換算後の周波数。印の位置は測った周波数のまま
        marks = source_view.leak_marks_view(self.state(freq_offset_hz=10 * MHZ))
        self.assertEqual((marks[1]["freq_hz"], marks[1]["tip"]), (1480 * MHZ, "内部の漏れの可能性 1490 MHz"))
        self.assertIn("leaks", screen_model.ui_view(self.state()))

    def test_mouse_reading_near_a_mark(self):
        marks = source_view.leak_marks_view(self.state())
        x = 1480 * MHZ + 3 * MHZ
        # 印の幅（1.18MHz）の外でも、1画素が 1MHz なら 5 画素（5MHz）の内なので拾う
        r = screen_model.mouse_reading([], x, -50.0, leaks=marks, px_hz=1 * MHZ)
        self.assertEqual(r["lines"][-1], "内部の漏れの可能性 1480 MHz")
        r = screen_model.mouse_reading([], x, -50.0, leaks=marks, px_hz=0.1 * MHZ)
        self.assertNotIn("内部の漏れの可能性 1480 MHz", r["lines"])
        r = screen_model.mouse_reading([], x, -50.0, zero_span=True, leaks=marks, px_hz=1 * MHZ)
        self.assertEqual(len(r["lines"]), 1)  # ゼロスパンは横軸が周波数ではない

    def test_peak_rows(self):
        leak = {"freq_hz": 1980 * MHZ, "kind": "sg_offset", "offset_hz": 1480 * MHZ}
        r = {"trace": 1, "unit": "dBm", "base": "noise_floor", "base_level": -76.0, "polarity": "peak",
             "peaks": [{"freq_hz": 1980 * MHZ, "level": -27.0, "diff_db": 49.0, "leak": leak},
                       {"freq_hz": 500 * MHZ, "level": -16.0, "diff_db": 60.0, "leak": None}]}
        rows = peak_view.result_view(r)["rows"]
        self.assertEqual((rows[0]["leak"], rows[0]["leak_tip"]),
                         (texts.LABELS["peak_leak"], "内部の漏れの可能性 1980 MHz（SG + 1480 MHz）"))
        self.assertEqual((rows[1]["leak"], rows[1]["leak_tip"]), ("", ""))


class InstrumentLeakTest(InstrumentCase):
    def setUp(self):
        super().setUp()
        self.connect(300 * MHZ, 2100 * MHZ)

    def test_state_and_peak_list(self):
        self.call("set_mode", mode="sg", sg_hz=500 * MHZ)
        st = self.single()
        lk = st["internal_leaks"]
        # イメージ除去「スピード」（初期値）は b3 の両方を合わせるので、b3 で動く 956MHz のスプリアスは消える（印も無い）
        self.assertEqual(st["sweep"]["image_reject_used"], "fast")
        self.assertEqual([x["freq_hz"] for x in lk["leaks"]], [1480 * MHZ, 1980 * MHZ])
        self.assertEqual(lk["tolerance_hz"], st["sweep"]["rbw_hz"] + st["sweep"]["step_hz"])
        pl = self.call("peak_list", trace=1, floor_margin_db=10.0)
        by = {round(p["freq_hz"] / MHZ): p for p in pl["peaks"]}
        leaks = {f for f, p in by.items() if p["leak"]}
        self.assertTrue({1480, 1980} <= leaks, by.keys())
        self.assertNotIn(500, leaks)  # SG の信号そのものは漏れではない
        self.assertEqual(by[1980]["leak"]["kind"], "sg_offset")

    def test_sa_has_no_leaks(self):
        st = self.single()
        self.assertEqual(st["internal_leaks"]["leaks"], [])
        pl = self.call("peak_list", trace=1, floor_margin_db=10.0)
        self.assertTrue(all(p["leak"] is None for p in pl["peaks"]))

    def test_view_option(self):
        self.assertIs(self.call("get_state")["view"]["leak_marks"], True)
        r = self.call("set_view", leak_marks=False)
        self.assertIs(r["view"]["leak_marks"], False)
        with self.assertRaises(Exception) as cm:
            self.call("set_view", leak_marks="no")
        self.assertEqual((cm.exception.code, cm.exception.field), ("bad_value", "leak_marks"))
        self.call("undo")  # 表示の設定は元に戻せる（設計J §3）
        self.assertIs(self.call("get_state")["view"]["leak_marks"], True)

    def test_step_sweep_result_has_every_step_leak(self):
        # ステップ掃引の結果は段ごとの SG + 1480MHz の漏れを持つ（SA に戻しても、そのトレースは SG で測ったもの）
        self.call("set_mode", mode="sg", sg_hz=500 * MHZ)
        self.call("set_step_sweep", start_hz=400 * MHZ, stop_hz=600 * MHZ, step_hz=100 * MHZ, combine="max", trace=2)
        r = self.call("step_sweep_start")
        self.wait_task(r["task_id"])
        self.call("set_mode", mode="sa")
        pl = self.call("peak_list", trace=2, floor_margin_db=10.0)
        flagged = {round(p["freq_hz"] / MHZ): p["leak"]["kind"] for p in pl["peaks"] if p["leak"]}
        for f in (1880, 1980, 2080):
            self.assertEqual(flagged.get(f), "sg_offset", (f, flagged))
        self.assertEqual(flagged.get(1480), "fixed")
        # ステップ掃引はいつも b3=1 だけで送る: 結果のトレースの 946MHz の山も漏れ（いまの選択がスピードでも）
        self.assertEqual(flagged.get(946), "fixed", flagged)
        self.assertNotIn(956, flagged)
        # 結果のトレースでないものは、いまのモードの決まりに従う（SA では漏れの印を付けない）
        pl = self.call("peak_list", trace=1, floor_margin_db=10.0)
        self.assertTrue(all(p["leak"] is None for p in pl["peaks"]))

    def test_step_sweep_result_keeps_measured_conditions(self):
        """結果のトレースの漏れは、それを測った条件（範囲・RBW・点の間隔・送った b3）で判定する（設計L §1.5）。あとで
        Center・Span・RBW を変えても一覧の「内部の漏れ」を保つ。格子を 400kHz ずらし、山が漏れの周波数から 600kHz
        離れる形にする（測った条件の幅 180kHz + 1MHz の内、狭くした後の幅 30kHz + 4kHz の外）。"""
        self.connect(300 * MHZ + 400_000, 2100 * MHZ + 400_000)
        self.call("set_mode", mode="sg", sg_hz=500 * MHZ)
        self.call("set_step_sweep", start_hz=400 * MHZ, stop_hz=600 * MHZ, step_hz=100 * MHZ, combine="max", trace=2)
        r = self.call("step_sweep_start")
        self.wait_task(r["task_id"])

        def flagged():
            pl = self.call("peak_list", trace=2, floor_margin_db=10.0)
            return {round(p["freq_hz"] / 100_000): p["leak"]["freq_hz"] for p in pl["peaks"] if p["leak"]}

        before = flagged()
        self.assertEqual(before.get(18794), 1880 * MHZ, before)
        self.assertEqual(before.get(14794), 1480 * MHZ, before)
        self.call("set_mode", mode="sa")
        self.call("set_sweep", center_hz=1880 * MHZ, span_hz=2 * MHZ, rbw_hz=30_000)
        sw = self.call("get_state")["sweep"]
        self.assertLess(dm.leak_tolerance_hz(sw["rbw_hz"], sw["step_hz"]), 600_000)
        self.assertEqual(flagged(), before)

    def test_leaks_follow_sg(self):
        self.call("set_mode", mode="sg", sg_hz=400 * MHZ)
        f = [x["freq_hz"] for x in self.call("get_state")["internal_leaks"]["leaks"]]
        self.assertIn(1880 * MHZ, f)
        self.call("set_mode", mode="tg")
        f = [x["freq_hz"] for x in self.call("get_state")["internal_leaks"]["leaks"]]
        self.assertEqual(f, [1480 * MHZ])  # スピード（b3 の両方）では 956MHz は消える
        self.call("set_sweep", image_reject="off")
        f = [x["freq_hz"] for x in self.call("get_state")["internal_leaks"]["leaks"]]
        self.assertEqual(f, [946 * MHZ, 1480 * MHZ])
        self.assertTrue(np.all(np.asarray(f) >= 300 * MHZ))


class MovingSpurTest(InstrumentCase):
    """SP 内部のスプリアス 956MHz は b3=1 で 946MHz に動く（T2）。印と一覧の漏れは、送った b3 の位置に付ける。"""

    def setUp(self):
        super().setUp()
        self.connect(900 * MHZ, 1100 * MHZ)
        self.call("set_mode", mode="sg", sg_hz=500 * MHZ)

    def peaks(self, trace=1):
        pl = self.call("peak_list", trace=trace, floor_margin_db=10.0)
        return {round(p["freq_hz"] / MHZ): p for p in pl["peaks"]}

    def test_image_reject_off_marks_946(self):
        self.call("set_sweep", image_reject="off")
        st = self.single()
        self.assertEqual([x["freq_hz"] for x in st["internal_leaks"]["leaks"]], [946 * MHZ])
        by = self.peaks()
        self.assertIn(946, by)
        self.assertEqual(by[946]["leak"]["freq_hz"], 946 * MHZ)
        self.assertEqual(source_view.leak_marks_view(st)[0]["tip"], "内部の漏れの可能性 946 MHz")

    def test_image_reject_both_has_no_moving_spur(self):
        for choice in ("fast", "accurate"):
            self.call("set_sweep", image_reject=choice)
            st = self.single()
            self.assertEqual(st["internal_leaks"]["leaks"], [], choice)
            by = self.peaks()
            self.assertFalse({946, 956} & set(by), (choice, sorted(by)))  # 山そのものが消える

    def test_step_sweep_uses_b3_1_positions(self):
        # 利用者の選択はスピードでも、ステップ掃引の間は b3=1 だけで送るので 946MHz に印（している間の状態）
        running = []
        self.inst.subscribe(lambda k, p: running.append(p["internal_leaks"]["leaks"])
                            if k == "state" and p["step_sweep"]["running"] else None)
        self.call("set_step_sweep", start_hz=450 * MHZ, stop_hz=550 * MHZ, step_hz=50 * MHZ, combine="max", trace=2)
        r = self.call("step_sweep_start")
        self.wait_task(r["task_id"])
        self.assertTrue(running)
        self.assertEqual([x["freq_hz"] for x in running[-1]], [946 * MHZ])
        # 終わったら選択（スピード）に戻り、動く漏れの印は無くなる
        self.assertEqual(self.call("get_state")["internal_leaks"]["leaks"], [])
        # 結果のトレースは b3=1 で測ったもの: 946MHz の山に漏れの印
        by = self.peaks(trace=2)
        self.assertEqual(by[946]["leak"]["freq_hz"], 946 * MHZ)


if __name__ == "__main__":
    unittest.main()
