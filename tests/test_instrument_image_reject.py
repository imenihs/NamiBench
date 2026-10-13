"""イメージ除去を3つから選ぶ（設計F §7）を計測器の本体で確かめる（シミュレータ）。

- 3つの選択の回数と送る b3 の並び・正確の帯で分けた区間（帯をまたぐ・帯の中だけ・外だけ・範囲の下限から）
- 状態の見積もり（3つ。正確 ≥ スピード > なし・区間は前置きの分だけ増える）・選択を変えたら蓄積を捨てる
- NF・ゼロスパンは選択によらずイメージ除去をしない・モードを変えても選択を引き継ぐ
- 前回の値・前の版の覚え書き／プリセットの image_cancel の読み替え・PNG の条件の行・CSV の見出し
"""
import json
import unittest

from namibench.core import report_layout as rl
from namibench.core.devices.gigast import protocol
from namibench.core.sweep import image_reject as ir
from namibench.core.sweep.model import SweepModel
from namibench.core.devices.gigast.driver import timing_fn
from namibench.core.types import Mode, SweepRequest

from tests.instrument_helpers import InstrumentCase

MHZ = 1_000_000
P = protocol.default_profile()


def model() -> SweepModel:
    return SweepModel(P, timing_fn(P))


def at(m, start, stop, choice, base=None, **kw):
    return m.apply(SweepRequest(start_hz=start, stop_hz=stop, edited=("start_hz", "stop_hz"), image_reject=choice,
                                **kw), base or m.default())


def plan(a):
    return [(s.point0, s.points, s.b3, s.in_band) for s in a.segments]


class TestPlan(unittest.TestCase):
    """掃引条件モデルが決める区間・回数・b3 の並び（設計F §7.1・§7.2）。"""

    def setUp(self):
        self.m = model()

    def test_three_choices_passes_and_b3(self):
        # 帯の中（500〜600MHz）: 正確 4回（0,1,0,1）・スピード 2回（0,1）・なし 1回（1）
        acc = at(self.m, 500 * MHZ, 600 * MHZ, "accurate")
        n = acc.axis.points
        self.assertEqual(plan(acc), [(0, n, (0, 1, 0, 1), True)])
        self.assertEqual(plan(at(self.m, 500 * MHZ, 600 * MHZ, "fast")), [(0, n, (0, 1), None)])
        self.assertEqual(plan(at(self.m, 500 * MHZ, 600 * MHZ, "off")), [(0, n, (1,), None)])

    def test_accurate_outside_bands_is_two_passes(self):
        # 帯の外（1200〜2900MHz）は正確でも 2回。スピードと同じ見積もり
        a = at(self.m, 1200 * MHZ, 2900 * MHZ, "accurate")
        self.assertEqual(plan(a), [(0, a.axis.points, (0, 1), False)])
        est = dict(a.image_reject_estimates)
        self.assertAlmostEqual(est["accurate"], est["fast"])
        self.assertAlmostEqual(est["fast"], 2 * est["off"])

    def test_accurate_crossing_band_edges_splits_segments(self):
        # 2900〜4100MHz（Step 1MHz）: 2950MHz と 4050MHz が境目。境目の点は右の区間（設計F §7.2）
        a = at(self.m, 2900 * MHZ, 4100 * MHZ, "accurate")
        self.assertEqual(a.axis.step_hz, MHZ)
        self.assertEqual(plan(a), [(0, 50, (0, 1), False), (50, 1100, (0, 1, 0, 1), True),
                                   (1150, 51, (0, 1), False)])
        f = a.axis.freqs()
        self.assertEqual(int(f[50]), 2950 * MHZ)  # 帯の下限ちょうどは帯の中（右の区間）
        self.assertEqual(int(f[1150]), 4050 * MHZ)  # 帯の上限ちょうどは帯の外（右の区間）
        # 区間ごとに前置きを置く（どれも下限から遠いので足りる）
        self.assertTrue(all(s.preroll_points > 0 and s.transient_head_points == 0 for s in a.segments))
        self.assertEqual(a.preroll_points, a.segments[0].preroll_points)

    def test_accurate_from_lower_limit_marks_transient(self):
        # 範囲の下限ちょうど（−100MHz）から始まる区間は前置きを置けず、先頭に「過渡の可能性」の印（設計F §7.2）
        a = at(self.m, -100 * MHZ, 1200 * MHZ, "accurate")
        first = a.segments[0]
        self.assertEqual((first.in_band, first.preroll_points), (True, 0))
        want = protocol.preroll_points_for(P, a.axis.step_hz, a.rbw_hz)
        self.assertEqual(first.transient_head_points, want)
        self.assertEqual(a.transient_head_points, want)
        # 帯の外の区間（1100MHz から）は前置きを置ける
        self.assertEqual((a.segments[1].in_band, a.segments[1].transient_head_points), (False, 0))
        self.assertEqual(a.segments[1].preroll_points, want)

    def test_estimates(self):
        # 見積もり = 区間ごとに 回数 ×（初バイトまで ＋（点数 ＋ 前置き）× 1点あたり）。区間が増えると前置きの分だけ増える
        a = at(self.m, 1000 * MHZ, 1200 * MHZ, "fast")
        first, per = protocol.timing(P, a.axis.step_hz, a.rbw_hz)
        est = dict(a.image_reject_estimates)
        self.assertEqual(list(est), ["accurate", "fast", "off"])
        segs = a.accurate_segments
        self.assertEqual([(s.points, s.passes) for s in segs], [(250, 4), (251, 2)])
        want_acc = sum(s.passes * (first + (s.points + s.preroll_points) * per) for s in segs)
        self.assertAlmostEqual(est["accurate"], want_acc)
        one = first + (a.axis.points + a.preroll_points) * per
        self.assertAlmostEqual(est["off"], one)
        self.assertAlmostEqual(est["fast"], 2 * one)
        self.assertGreater(est["accurate"], est["fast"])
        self.assertGreater(est["fast"], est["off"])
        self.assertAlmostEqual(a.estimated_sweep_s, est["fast"])
        # 2つ目の区間の前置きの分だけ、同じ回数で1つの区間にしたときより長い
        joined = 4 * (first + (250 + segs[0].preroll_points) * per) + 2 * (first + 251 * per)
        self.assertAlmostEqual(est["accurate"] - joined, 2 * segs[1].preroll_points * per)

    def test_spec_example_0_to_1ghz(self):
        # 設計F §7.4 の例: 0〜1GHz で「正確（約 3.8 秒）」「スピード（約 1.9 秒）」「なし（約 1.0 秒）」
        est = dict(at(self.m, 0, 1000 * MHZ, "fast").image_reject_estimates)
        self.assertEqual({k: f"{v:.1f}" for k, v in est.items()}, {"accurate": "3.8", "fast": "1.9", "off": "1.0"})

    def test_nf_and_zero_span_do_not_reject(self):
        # NF（HOT/COLD の2回）とゼロスパンは選択によらずイメージ除去をしない（設計F §7.1）
        nf = at(self.m, 500 * MHZ, 600 * MHZ, "accurate", mode=Mode.NF)
        self.assertEqual((nf.image_reject, nf.image_reject_used), ("accurate", "off"))
        self.assertEqual([s.b3 for s in nf.segments], [(1,)])
        self.assertEqual(len({round(v, 9) for _c, v in nf.image_reject_estimates}), 1)
        zs = self.m.apply(SweepRequest(center_hz=500 * MHZ, span_hz=0, edited=("center_hz", "span_hz"),
                                       image_reject="fast"), self.m.default())
        self.assertEqual((zs.image_reject, zs.image_reject_used), ("fast", "off"))
        self.assertEqual([s.b3 for s in zs.segments], [(1,)])

    def test_selection_survives_mode_change(self):
        a = at(self.m, 500 * MHZ, 600 * MHZ, "accurate")
        for mode in (Mode.TG, Mode.SG, Mode.S11, Mode.NF, Mode.SA):
            a = self.m.apply(SweepRequest(mode=mode, sg_hz=100 * MHZ if mode == Mode.SG else None), a)
            self.assertEqual(a.image_reject, "accurate", mode)

    def test_unknown_choice_rejected(self):
        with self.assertRaises(ValueError):
            self.m.apply(SweepRequest(image_reject="slow"), self.m.default())


class TestConditionKind(unittest.TestCase):
    """選択を変えたら蓄積を捨てる。NF・ゼロスパンでは測る値が変わらないので捨てない（設計F §7.3・§5.3）。"""

    def test_kind(self):
        from namibench.core.instrument_display import _condition_kind
        from namibench.core.trace import ConditionChange

        m = model()
        sa = at(m, 500 * MHZ, 600 * MHZ, "fast")
        self.assertEqual(_condition_kind(sa, m.apply(SweepRequest(image_reject="accurate"), sa)),
                         ConditionChange.IMAGE_REJECT)
        nf = m.apply(SweepRequest(mode=Mode.NF), sa)
        self.assertIsNone(_condition_kind(nf, m.apply(SweepRequest(image_reject="accurate"), nf)))
        zs = m.apply(SweepRequest(span_hz=0, edited=("span_hz",)), sa)
        self.assertIsNone(_condition_kind(zs, m.apply(SweepRequest(image_reject="off"), zs)))


class TestReadChoice(unittest.TestCase):
    def test_legacy_and_new_keys(self):
        self.assertEqual(ir.read_choice({"image_cancel": True}), "fast")
        self.assertEqual(ir.read_choice({"image_cancel": False}), "off")
        self.assertEqual(ir.read_choice({"image_reject": "accurate", "image_cancel": False}), "accurate")
        self.assertIsNone(ir.read_choice({}))
        self.assertIsNone(ir.read_choice(None))
        with self.assertRaises(ValueError):
            ir.read_choice({"image_reject": "slow"})


class TestInstrument(InstrumentCase):
    def test_state_exposes_estimates_and_accurate_segments(self):
        self.connect(1000 * MHZ, 1200 * MHZ)
        sw = self.call("get_state")["sweep"]
        self.assertEqual(sw["image_reject"], "fast")  # 初期値（設計F §7.3）
        self.assertEqual(sw["image_reject_used"], "fast")
        est = sw["image_reject_estimates"]
        self.assertEqual(set(est), {"accurate", "fast", "off"})
        self.assertGreater(est["accurate"], est["fast"])
        self.assertGreater(est["fast"], est["off"])
        self.assertAlmostEqual(sw["estimated_sweep_s"], est["fast"])
        segs = sw["accurate_segments"]
        self.assertEqual([(s["passes"], s["start_hz"]) for s in segs], [(4, 1000 * MHZ), (2, 1100 * MHZ)])
        self.assertEqual(segs[-1]["stop_hz"], sw["start_hz"] + sw["step_hz"] * (sw["points"] - 1))
        self.call("set_sweep", image_reject="accurate")
        sw = self.call("get_state")["sweep"]
        self.assertAlmostEqual(sw["estimated_sweep_s"], est["accurate"])

    def test_bad_choice_is_command_error(self):
        from namibench.core.instrument_common import CommandError

        with self.assertRaises(CommandError) as cm:
            self.call("set_sweep", image_reject="slow")
        self.assertEqual((cm.exception.code, cm.exception.field), ("bad_value", "image_reject"))

    def test_changing_selection_discards_accumulation(self):
        # 選択を変えるのは値の意味が変わる条件の変更（蓄積を捨てる: 設計F §7.3・§5.3）
        self.connect(500 * MHZ, 600 * MHZ)
        self.call("set_trace", number=1, action="max_hold")
        self.single()
        self.clear_events()
        self.call("set_sweep", image_reject="accurate")
        p = self.wait_for(lambda k, p: k == "notice" and p["code"] == "trace_reset")
        self.assertEqual(p["detail"]["cause"], "image_reject")

    def test_accurate_measures_and_shows(self):
        self.connect(1000 * MHZ, 1200 * MHZ)
        self.call("set_sweep", image_reject="accurate")
        self.single()
        st = self.call("get_state")
        t1 = next(t for t in st["traces"] if t["number"] == 1)
        self.assertTrue(t1["has_values"])

    def test_nf_keeps_selection(self):
        # NF では使わないが選択は持ち続け、SA に戻ると使う（設計F §7.3: モードを変えても引き継ぐ）
        self.connect(500 * MHZ, 600 * MHZ)
        self.call("set_sweep", image_reject="accurate")
        self.call("set_mode", mode="nf")
        sw = self.call("get_state")["sweep"]
        self.assertEqual((sw["image_reject"], sw["image_reject_used"]), ("accurate", "off"))
        self.call("set_sweep", image_reject="fast")
        self.call("set_mode", mode="sa")
        sw = self.call("get_state")["sweep"]
        self.assertEqual((sw["image_reject"], sw["image_reject_used"]), ("fast", "fast"))

    def test_png_condition_line_uses_choice_used(self):
        self.connect(500 * MHZ, 600 * MHZ)
        self.call("set_sweep", image_reject="accurate")
        st = self.call("get_state")
        data = rl.build_data(st, ref_level=0.0, db_per_div=10.0, unit="dBm", mode_name="SA", traces=[], markers=[],
                             warnings=[], app_version="0", norm_reasons={})
        self.assertEqual(data["conditions"]["image_reject"], "accurate")
        text = " ".join(" ".join(items) for _color, items in rl.band_rows(data))
        self.assertIn("イメージ除去 正確", text)
        self.assertNotIn("イメージキャンセル", text)
        self.call("set_mode", mode="nf")
        data = rl.build_data(self.call("get_state"), ref_level=0.0, db_per_div=10.0, unit="dBm", mode_name="NF",
                             traces=[], markers=[], warnings=[], app_version="0", norm_reasons={})
        self.assertIn("イメージ除去 なし", " ".join(" ".join(items) for _color, items in rl.band_rows(data)))

    def test_csv_header_has_image_reject(self):
        self.connect(500 * MHZ, 600 * MHZ)
        self.call("set_sweep", image_reject="accurate")
        self.single()
        path = self.root / "t.csv"
        self.wait_task(self.call("export_csv", path=str(path), traces=[1])["task_id"])
        first = path.read_text(encoding="ascii").splitlines()[0]
        meta = json.loads(first[len("# meta: "):])
        self.assertEqual(meta["conditions"]["image_reject"], "accurate")
        self.assertNotIn("image_cancel", meta["conditions"])


class TestRestore(InstrumentCase):
    """起動は前回の値。前の版の覚え書きの image_cancel は読み替え、無ければプロファイルの初期値（設計F §7.3）。"""

    def restart_with_state(self, state):
        self.inst.shutdown()
        path = self.root / "data" / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        d = json.loads(path.read_text("utf-8")) if path.exists() else {"format": "pygigast-settings", "version": 1}
        d["state"] = state
        path.write_text(json.dumps(d), encoding="utf-8")
        self.inst = self.make_instrument()
        return self.call("get_state")["sweep"]["image_reject"]

    def test_previous_value_and_legacy(self):
        base = {"start_hz": 100 * MHZ, "stop_hz": 200 * MHZ, "mode": "sa"}
        self.assertEqual(self.restart_with_state({**base, "image_reject": "accurate"}), "accurate")
        self.assertEqual(self.restart_with_state({**base, "image_cancel": True}), "fast")
        self.assertEqual(self.restart_with_state({**base, "image_cancel": False}), "off")
        self.assertEqual(self.restart_with_state(base), "fast")  # 無ければプロファイルの初期値

    def test_saved_on_shutdown(self):
        self.call("set_sweep", image_reject="off")
        self.inst.shutdown()
        d = json.loads((self.root / "data" / "settings.json").read_text("utf-8"))
        self.assertEqual(d["state"]["image_reject"], "off")
        self.assertNotIn("image_cancel", d["state"])
        self.inst = self.make_instrument()
        self.assertEqual(self.call("get_state")["sweep"]["image_reject"], "off")


class TestPresets(InstrumentCase):
    def test_preset_keeps_choice_and_reads_legacy(self):
        self.call("set_sweep", image_reject="accurate")
        saved = self.call("preset_save", name="正確")
        path = self.root / "data" / "presets" / f"{saved['id']}.json"
        d = json.loads(path.read_text("utf-8"))
        self.assertEqual(d["content"]["sweep"]["image_reject"], "accurate")
        self.call("set_sweep", image_reject="off")
        self.call("preset_load", id=saved["id"])
        self.assertEqual(self.call("get_state")["sweep"]["image_reject"], "accurate")
        # 前の版のプリセット（image_cancel）は true → スピード、false → なし
        for ic, want in ((True, "fast"), (False, "off")):
            sw = d["content"]["sweep"]
            sw.pop("image_reject", None)
            sw["image_cancel"] = ic
            path.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            self.call("set_sweep", image_reject="accurate")
            self.call("preset_load", id=saved["id"])
            self.assertEqual(self.call("get_state")["sweep"]["image_reject"], want)


if __name__ == "__main__":
    unittest.main()
