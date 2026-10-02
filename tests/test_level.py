"""設計D（旧校正ファイルの取り込みとレベル換算）の試験。

ref/original/ の実物の校正ファイルは git の管理外なので、ある時だけ使う（skipUnless）。
それ以外は試験の中で作った合成のファイルを使う。
"""
from __future__ import annotations

import json
import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path
from typing import Optional

import numpy as np

from pygigast.core.devices.base import load_profile
from pygigast.core.level.calibration import Calibration, CalibrationStore, default_root, load_file
from pygigast.core.level.convert import ConvertedLevels, LevelConverter, dbm_to_watt
from pygigast.core.level.legacy_cal import LegacyCal, LegacyCalError, parse_legacy_cal, vb_val
from pygigast.core.types import AdoptedSweep, FreqAxis, Mode

ROOT = Path(__file__).resolve().parent.parent
PROFILE = load_profile(ROOT / "pygigast" / "core" / "devices" / "gigast" / "profile.json")
REAL_BA205 = ROOT / "ref" / "original" / "ba205.txt"
REAL_GIGAST = ROOT / "ref" / "original" / "GigaSt" / "gigast.txt"

MHZ = 1_000_000
JST = timezone(timedelta(hours=9))


# ---------------------------------------------------------------------------
# 合成の旧校正ファイル
# ---------------------------------------------------------------------------

def _line(fields: list[str]) -> str:
    """6文字 × 16個（左詰め・空白埋め）＋ "." ＋ CR（行末の CR LF は結合時に付ける）。"""
    fields = list(fields) + [""] * (16 - len(fields))
    return "".join(f"{s:<6}"[:6] for s in fields) + ".\r"


def synthetic_hf() -> list[list[int]]:
    """隣の点の差が最大 5dB の合成の表（band の境目では値が飛ぶ）。"""
    rng = random.Random(1234)
    hf = []
    for band in range(12):
        v = rng.randint(-8, 8) + band * 2
        row = []
        for _ in range(11):
            row.append(v)
            v += rng.randint(-5, 5)
        hf.append(row)
    return hf


def build_legacy_bytes(
    adj30k: str = "-2",
    adj15k: str = "3",
    common_bias: str = "-1.5",
    enr: str = "142",
    nf_amp: str = "-4",
    hf: Optional[list[list[int]]] = None,
    unit: str = "dBm",
    lines_total: int = 46,
) -> bytes:
    hf = hf if hf is not None else synthetic_hf()
    l1 = ["1000", "0", "10", "0", "500", "0", "10", "0", "180", "Off", "COMx", unit, "0", adj30k, adj15k, common_bias]
    l2 = ["615", "556", "1000", "0", "Off", enr, nf_amp, "Line"]
    lines = [_line(l1), _line(l2)] + [_line([str(v) for v in row] + ["0"] * 5) for row in hf]
    while len(lines) < lines_total:
        lines.append(_line(["00"] * 16))
    return ("\r\n".join(lines) + "\r\n").encode("cp932")


# ---------------------------------------------------------------------------
# 旧アプリの計算の参照実装（設計D §5.4 確かめ方1）
# ---------------------------------------------------------------------------

def _vb_round(x: Fraction) -> int:
    """.NET の Math.Round（偶数丸め）。Fraction の round は偶数丸め。"""
    return round(x)


def legacy_reference_dbm(ad: int, f_hz: int, legacy: LegacyCal, rbw_hz: int) -> Optional[float]:
    """旧アプリの SA/SG（Div=10）の換算をそのまま書いたもの。画素への丸めは除く。

    周波数は MHz にして 0.5 を引き、偶数丸めで整数 MHz にしてから補間する。
    丸めた周波数が 0 未満か 12100 を超えると旧アプリは補正を更新しない（不定）ので None を返す。
    """
    fk = _vb_round(Fraction(f_hz, MHZ) - Fraction(1, 2))
    if fk < 0 or fk > 12100:
        return None
    hf = [list(r) for r in legacy.hf] + [[0] * 16]  # 行12は常に0
    b = (fk // 1000) % 13
    seg = (fk // 100) % 10
    frac = fk % 100
    row = hf[b] + [0] * (16 - len(hf[b]))
    corr = row[seg] + (row[seg + 1] - row[seg]) * frac / 100
    adj = {180_000: 0.0, 30_000: legacy.adj30k, 15_000: legacy.adj15k}[rbw_hz]
    return ad * 80 / 1986 - 80 - legacy.common_bias + adj + corr


def make_adopted(freqs_start: int, step: int, points: int, mode: Mode = Mode.SA, rbw_hz: int = 180_000) -> AdoptedSweep:
    axis = FreqAxis(start_hz=freqs_start, step_hz=step, points=points)
    return AdoptedSweep(
        start_hz=freqs_start,
        stop_hz=axis.last_hz,
        axis=axis,
        rbw_hz=rbw_hz,
        rbw_preference_hz=rbw_hz,
        mode=mode,
        sg_hz=None,
        image_reject="off",
        preroll_points=0,
        transient_head_points=0,
        estimated_sweep_s=0.0,
    )


def make_cal(legacy: LegacyCal) -> Calibration:
    return Calibration.from_legacy(
        legacy, model="gigast-v5", serial="TEST0001", file_name="synthetic.txt",
        sha256="0" * 64, created_at=datetime(2026, 9, 30, 21, 0, 0, tzinfo=JST),
    )


# ---------------------------------------------------------------------------
# Val()
# ---------------------------------------------------------------------------

class TestVbVal(unittest.TestCase):
    def test_cases(self):
        cases = {
            "": 0.0, "      ": 0.0, "12": 12.0, "  -3": -3.0, "+5": 5.0, "1.5": 1.5, "-.5": -0.5,
            "3.": 3.0, "12abc": 12.0, "1.2.3": 1.2, "abc": 0.0, "-": 0.0, ".": 0.0, "- 1": 0.0,
            "1 2": 1.0, "\t7": 7.0, "--1": 0.0, "0x10": 0.0, "1e3": 1.0,
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(vb_val(text), want)


# ---------------------------------------------------------------------------
# 旧ファイルの読込
# ---------------------------------------------------------------------------

class TestParseLegacy(unittest.TestCase):
    def test_synthetic(self):
        hf = synthetic_hf()
        lc = parse_legacy_cal(build_legacy_bytes(hf=hf))
        self.assertEqual(lc.adj30k, -2.0)
        self.assertEqual(lc.adj15k, 3.0)
        self.assertEqual(lc.common_bias, -1.5)
        self.assertEqual(lc.enr_x10, 142.0)
        self.assertEqual(lc.nf_amp_raw, -4.0)
        self.assertEqual([list(r) for r in lc.hf], hf)

    def test_japanese_unit_field_keeps_char_width(self):
        # 表示単位の欄に日本語（CP932 で2バイト）が入っても、固定幅は文字単位なので後ろの欄がずれない
        lc = parse_legacy_cal(build_legacy_bytes(unit="ﾃﾞｼﾍﾞﾙ", adj30k="-7"))
        self.assertEqual(lc.adj30k, -7.0)
        lc = parse_legacy_cal(build_legacy_bytes(unit="デシベル", adj30k="-7"))
        self.assertEqual(lc.adj30k, -7.0)

    def test_only_14_lines_ok(self):
        lc = parse_legacy_cal(build_legacy_bytes(lines_total=14))
        self.assertEqual(len(lc.hf), 12)

    def test_lf_only_line_endings(self):
        data = build_legacy_bytes().replace(b"\r", b"")
        lc = parse_legacy_cal(data)
        self.assertEqual([list(r) for r in lc.hf], synthetic_hf())

    def test_too_few_lines(self):
        text = build_legacy_bytes().decode("cp932").split("\n")
        data = "\n".join(text[:13]).encode("cp932")
        with self.assertRaises(LegacyCalError) as cm:
            parse_legacy_cal(data)
        self.assertEqual(cm.exception.reason, "too_few_lines")
        self.assertEqual(cm.exception.line, 14)
        self.assertIsNone(cm.exception.field)

    def test_empty_file(self):
        with self.assertRaises(LegacyCalError):
            parse_legacy_cal(b"")

    def test_non_number_field_in_hf(self):
        hf = synthetic_hf()
        lines = build_legacy_bytes(hf=hf).decode("cp932").split("\n")
        # 7行目（band 4）の欄 3 を数字で始まらないものにする
        row = lines[6]
        lines[6] = row[:18] + "x1    " + row[24:]
        with self.assertRaises(LegacyCalError) as cm:
            parse_legacy_cal("\n".join(lines).encode("cp932"))
        self.assertEqual(cm.exception.reason, "not_a_number")
        self.assertEqual((cm.exception.line, cm.exception.field), (7, 3))

    def test_sign_only_field_is_unreadable(self):
        lines = build_legacy_bytes().decode("cp932").split("\n")
        lines[13] = "-     " + lines[13][6:]
        with self.assertRaises(LegacyCalError) as cm:
            parse_legacy_cal("\n".join(lines).encode("cp932"))
        self.assertEqual((cm.exception.line, cm.exception.field), (14, 0))

    def test_blank_hf_field_is_zero(self):
        lines = build_legacy_bytes().decode("cp932").split("\n")
        lines[2] = "      " + lines[2][6:]
        lc = parse_legacy_cal("\n".join(lines).encode("cp932"))
        self.assertEqual(lc.hf[0][0], 0)

    def test_short_hf_line_is_zero(self):
        # 行が短く欄が無いときは空欄と同じ扱い（0）
        lines = build_legacy_bytes().decode("cp932").split("\n")
        lines[3] = lines[3][:12]
        lc = parse_legacy_cal("\n".join(lines).encode("cp932"))
        self.assertEqual(lc.hf[1][2:], (0,) * 9)

    def test_garbage_in_line1_is_zero(self):
        # 1〜2行目は読めなくても 0（設計D §5.1 の読めない条件は 3〜14行目だけ）
        lc = parse_legacy_cal(build_legacy_bytes(common_bias="abc"))
        self.assertEqual(lc.common_bias, 0.0)

    def test_large_values_not_rejected(self):
        hf = [[30] * 11 for _ in range(12)]
        hf[11] = [99, -99, 0, 0, 0, 0, 0, 0, 0, 0, 0]
        lc = parse_legacy_cal(build_legacy_bytes(hf=hf))
        self.assertEqual(lc.hf[11][:2], (99, -99))

    def test_graph_lines_not_read(self):
        # 15行目以降が壊れていても読める
        data = build_legacy_bytes(lines_total=14) + b"zzzzzz\xff\xfe garbage\r\n"
        parse_legacy_cal(data)


@unittest.skipUnless(REAL_BA205.is_file(), "ref/original/ba205.txt が無い（git の管理外）")
class TestRealBa205(unittest.TestCase):
    """設計D §5.1 に記録した実物の値。"""

    def check(self, path: Path):
        lc = parse_legacy_cal(path.read_bytes())
        self.assertEqual(lc.adj30k, -1.0)
        self.assertEqual(lc.adj15k, -1.0)
        self.assertEqual(lc.common_bias, -1.0)
        self.assertEqual(lc.enr_x10, 142.0)
        self.assertEqual(lc.nf_amp_raw, -4.0)
        self.assertEqual(lc.hf[0], (2, 0, -1, -1, -2, -2, -2, -2, -2, -3, -3))
        self.assertEqual(lc.hf[11], (27, 29, 28, 28, 30, 29, 29, 30, 28, 28, 29))
        return lc

    def test_ba205(self):
        lc = self.check(REAL_BA205)
        cal = make_cal(lc)
        self.assertEqual(cal.level_offset_db, 1.0)
        self.assertEqual(cal.rbw_offset_db, {180_000: 0.0, 30_000: -1.0, 15_000: -1.0})
        self.assertEqual(cal.nf.enr_db, 14.2)

    @unittest.skipUnless(REAL_GIGAST.is_file(), "ref/original/GigaSt/gigast.txt が無い")
    def test_gigast_txt_same_values(self):
        self.check(REAL_GIGAST)

    def test_legacy_reference_whole_range(self):
        lc = parse_legacy_cal(REAL_BA205.read_bytes())
        _compare_with_legacy(self, lc)


# ---------------------------------------------------------------------------
# 新しい校正データ
# ---------------------------------------------------------------------------

class TestCalibration(unittest.TestCase):
    def test_sign_convention(self):
        lc = parse_legacy_cal(build_legacy_bytes(common_bias="-1.5", adj30k="-2", adj15k="3"))
        cal = make_cal(lc)
        # 旧 Common-Bias は減算 → 新しい形式は加算（符号反転）
        self.assertEqual(cal.level_offset_db, 1.5)
        self.assertEqual(cal.rbw_offset_db, {180_000: 0.0, 30_000: -2.0, 15_000: 3.0})
        self.assertTrue(all(isinstance(k, int) for k in cal.rbw_offset_db))
        self.assertEqual(cal.b3_basis, 1)
        self.assertEqual(cal.source.kind, "legacy_file")
        self.assertEqual(cal.created_at, "2026-09-30T21:00:00+09:00")
        self.assertEqual(len(cal.bands), 12)
        self.assertEqual(cal.bands[3].start_hz, 3_000_000_000)
        self.assertEqual(cal.freq_step_hz, 100_000_000)

    def test_zero_common_bias_is_not_negative_zero(self):
        cal = make_cal(parse_legacy_cal(build_legacy_bytes(common_bias="0")))
        self.assertEqual(json.dumps(cal.to_dict()["level_offset_db"]), "0.0")

    def test_round_trip_json(self):
        cal = make_cal(parse_legacy_cal(build_legacy_bytes()))
        d = json.loads(json.dumps(cal.to_dict()))
        self.assertEqual(d["format"], "pygigast-calibration")
        self.assertEqual(d["version"], 1)
        self.assertEqual(d["device"], {"model": "gigast-v5", "serial": "TEST0001"})
        self.assertEqual(set(d["rbw_offset_db"]), {"180000", "30000", "15000"})
        self.assertEqual(d["freq_response_db"]["step_hz"], 100_000_000)
        back = Calibration.from_dict(d)
        self.assertEqual(back, cal)

    def test_from_dict_rejects(self):
        d = make_cal(parse_legacy_cal(build_legacy_bytes())).to_dict()
        for mutate in (
            lambda x: x.__setitem__("format", "other"),
            lambda x: x.__setitem__("version", 2),
            lambda x: x["freq_response_db"]["bands"][1].__setitem__("start_hz", 5),
            lambda x: x["freq_response_db"]["bands"][1].__setitem__("values", [0] * 10),
            lambda x: x["freq_response_db"].__setitem__("step_hz", 1e8 + 0.5),
        ):
            dd = json.loads(json.dumps(d))
            mutate(dd)
            with self.assertRaises((ValueError, TypeError)):
                Calibration.from_dict(dd)

    def test_naive_datetime_rejected(self):
        with self.assertRaises(ValueError):
            Calibration.from_legacy(
                parse_legacy_cal(build_legacy_bytes()), "gigast-v5", "S", "f", "0", datetime(2026, 1, 1)
            )


class TestCalibrationStore(unittest.TestCase):
    def test_save_history_current(self):
        with tempfile.TemporaryDirectory() as td:
            store = CalibrationStore(Path(td))
            self.assertIsNone(store.load_current("TEST0001"))
            self.assertEqual(store.history("TEST0001"), [])

            c1 = make_cal(parse_legacy_cal(build_legacy_bytes(common_bias="-1")))
            p1 = store.save(c1)
            self.assertEqual(p1.parent, Path(td) / "TEST0001")
            self.assertEqual(p1.name, "20260930T210000+0900.json")
            self.assertEqual(store.load_current("TEST0001"), c1)

            # 同じ日時でも上書きしない
            c2 = make_cal(parse_legacy_cal(build_legacy_bytes(common_bias="-2")))
            p2 = store.save(c2)
            self.assertNotEqual(p1, p2)
            self.assertEqual(load_file(p1), c1)
            self.assertEqual(store.load_current("TEST0001"), c2)
            self.assertEqual(store.history("TEST0001"), sorted([p1, p2]))

            # 履歴から選び直せる
            store.set_current("TEST0001", p1.name)
            self.assertEqual(store.load_current("TEST0001"), c1)
            ptr = json.loads((Path(td) / "TEST0001" / "current.json").read_text(encoding="utf-8"))
            self.assertEqual(ptr["file"], p1.name)

            # current にしない保存
            store.save(c2, make_current=False)
            self.assertEqual(store.load_current("TEST0001"), c1)
            self.assertEqual(len(store.history("TEST0001")), 3)

    def test_bad_names(self):
        with tempfile.TemporaryDirectory() as td:
            store = CalibrationStore(Path(td))
            for serial in ("", "..", "a/b", "a\\b", ".hidden"):
                with self.subTest(serial=serial):
                    with self.assertRaises(ValueError):
                        store.history(serial)
            store.save(make_cal(parse_legacy_cal(build_legacy_bytes())))
            with self.assertRaises(ValueError):
                store.set_current("TEST0001", "../x.json")
            with self.assertRaises(FileNotFoundError):
                store.set_current("TEST0001", "missing.json")

    def test_default_root(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"APPDATA": str(Path("X:/appdata"))}):
            self.assertEqual(default_root(), Path("X:/appdata") / "pyGigaSt" / "calibration")
        env = {k: v for k, v in os.environ.items() if k != "APPDATA"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(default_root(), Path.home() / ".pygigast" / "calibration")


# ---------------------------------------------------------------------------
# 換算
# ---------------------------------------------------------------------------

def _simple_cal() -> Calibration:
    hf = [[b * 10 + k for k in range(11)] for b in range(12)]  # band b・点 k の値 = 10b + k
    return make_cal(parse_legacy_cal(build_legacy_bytes(hf=hf, common_bias="-1", adj30k="-2", adj15k="3")))


class TestConvert(unittest.TestCase):
    def setUp(self):
        self.cal = _simple_cal()
        self.conv = LevelConverter(PROFILE, self.cal)

    def test_uint16_overflow_regression(self):
        # AD 1986 は uint16 のまま ×80 すると桁あふれする（T10）。補正の前は 0 dBm
        conv = LevelConverter(PROFILE, None)
        ad = np.array([1986, 0, 3972], dtype=np.uint16)
        res = conv.to_dbm(make_adopted(500 * MHZ, MHZ, 3), ad)
        np.testing.assert_allclose(res.dbm, [0.0, -80.0, 80.0])
        self.assertEqual(res.dbm.dtype, np.float64)

    def test_no_calibration(self):
        conv = LevelConverter(PROFILE, None)
        res = conv.to_dbm(make_adopted(-10 * MHZ, 5_000 * MHZ, 4), np.full(4, 993, dtype=np.uint16))
        self.assertTrue(res.no_calibration)
        np.testing.assert_allclose(res.dbm, np.full(4, -40.0))
        self.assertFalse(res.out_of_cal_range.any())

    def test_corr_points_and_interpolation(self):
        f = np.array([0, 50 * MHZ, 100 * MHZ, 999_999_999, 1_000_000_000, 1_000_000_001,
                      2_350 * MHZ, 11_999_999_999], dtype=np.int64)
        c, flag = self.conv.corr(f)
        want = [0.0, 0.5, 1.0, 9.99999999, 10.0, 10.00000001, 23.5, 119.99999999]
        np.testing.assert_allclose(c, want, atol=1e-9)
        self.assertFalse(flag.any())

    def test_band_edge_uses_next_band(self):
        # band 0 の最後の点（=10）と band 1 の最初の点（=10）が同じだと区別できないので、違う値の表で確かめる
        hf = [[0] * 11 for _ in range(12)]
        hf[0][10] = 7
        hf[1][0] = -3
        conv = LevelConverter(PROFILE, make_cal(parse_legacy_cal(build_legacy_bytes(hf=hf))))
        c, _ = conv.corr(np.array([1_000_000_000, 999_999_999], dtype=np.int64))
        self.assertEqual(c[0], -3.0)
        self.assertAlmostEqual(c[1], 7.0, places=6)

    def test_negative_and_above_range(self):
        f = np.array([-1, -100 * MHZ, 12_000 * MHZ, 12_100 * MHZ, 11_999 * MHZ], dtype=np.int64)
        c, flag = self.conv.corr(f)
        np.testing.assert_allclose(c, [0.0, 0.0, 0.0, 0.0, 119.99])
        self.assertEqual(flag.tolist(), [True, True, True, True, False])
        # band 0 の最初の値が 0 でない表で f < 0 を確かめる
        hf = [[5] * 11 for _ in range(12)]
        conv = LevelConverter(PROFILE, make_cal(parse_legacy_cal(build_legacy_bytes(hf=hf))))
        c, flag = conv.corr(np.array([-1, 12_050 * MHZ], dtype=np.int64))
        self.assertEqual(c.tolist(), [5.0, 0.0])
        self.assertEqual(flag.tolist(), [True, True])

    def test_corr_rejects_float_freqs(self):
        with self.assertRaises(TypeError):
            self.conv.corr(np.array([1.0e9]))

    def test_sa_vs_tg(self):
        ad = np.full(3, 1986, dtype=np.uint16)
        f0 = 2_350 * MHZ  # corr = 23.5
        sa = self.conv.to_dbm(make_adopted(f0, 0, 3, Mode.SA, 30_000), ad)
        sg = self.conv.to_dbm(make_adopted(f0, 0, 3, Mode.SG, 15_000), ad)
        tg = self.conv.to_dbm(make_adopted(f0, 0, 3, Mode.TG, 180_000), ad)
        s11 = self.conv.to_dbm(make_adopted(f0, 0, 3, Mode.S11, 180_000), ad)
        # level_offset = −CommonBias = +1
        np.testing.assert_allclose(sa.dbm, 1.0 - 2.0 + 23.5)
        np.testing.assert_allclose(sg.dbm, 1.0 + 3.0 + 23.5)
        np.testing.assert_allclose(tg.dbm, 1.0)
        np.testing.assert_allclose(s11.dbm, 1.0)
        for r in (sa, sg, tg, s11):
            self.assertFalse(r.no_calibration)
            self.assertFalse(r.rbw_offset_missing)

    def test_tg_no_flag_even_out_of_range(self):
        res = self.conv.to_dbm(make_adopted(-10 * MHZ, 0, 2, Mode.TG), np.zeros(2, dtype=np.uint16))
        self.assertFalse(res.out_of_cal_range.any())

    def test_unknown_rbw_uses_zero_and_marks(self):
        res = self.conv.to_dbm(make_adopted(0, 0, 1, Mode.SA, 100_000), np.array([1986], dtype=np.uint16))
        self.assertTrue(res.rbw_offset_missing)
        np.testing.assert_allclose(res.dbm, [1.0])

    def test_nf_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            self.conv.to_dbm(make_adopted(0, MHZ, 2, Mode.NF), np.zeros(2, dtype=np.uint16))

    def test_length_mismatch(self):
        with self.assertRaises(ValueError):
            self.conv.to_dbm(make_adopted(0, MHZ, 3), np.zeros(2, dtype=np.uint16))

    def test_dbm_to_watt(self):
        np.testing.assert_allclose(dbm_to_watt(np.array([30.0, 0.0, -30.0])), [1.0, 1e-3, 1e-6])

    def test_synthetic_legacy_comparison(self):
        _compare_with_legacy(self, parse_legacy_cal(build_legacy_bytes()))


def _compare_with_legacy(tc: unittest.TestCase, lc: LegacyCal) -> None:
    """新しい換算と旧アプリの参照実装の差が 0.1dB 以内（設計D §5.4 確かめ方1）。

    0〜12100MHz を 1MHz 刻みと、ランダムな Hz の周波数で、3つの RBW・いくつかの AD で比べる。
    """
    cal = make_cal(lc)
    conv = LevelConverter(PROFILE, cal)
    rng = random.Random(20260930)
    grid = [m * MHZ for m in range(0, 12_101)]
    rand = [rng.randint(0, 12_100 * MHZ) for _ in range(3000)]
    # 丸めの境目（整数 MHz ちょうど・0.5MHz）と band の境目の前後
    edges = []
    for n in range(0, 13):
        for d in (-500_001, -500_000, -499_999, -1, 0, 1, 499_999, 500_000, 500_001, 999_999):
            fz = n * 1000 * MHZ + d
            if 0 <= fz <= 12_100 * MHZ:
                edges.append(fz)
    freqs = grid + rand + edges
    fa = np.array(freqs, dtype=np.int64)
    worst = 0.0
    for rbw in (180_000, 30_000, 15_000):
        for ad_val in (0, 1000, 1986, 4095):
            # 周波数が等間隔でないので、to_dbm の代わりに同じ部品（ad_to_db・corr）で組み立てる
            base = conv.ad_to_db(np.full(len(freqs), ad_val, dtype=np.uint16))
            c, _ = conv.corr(fa)
            new = base + cal.level_offset_db + cal.rbw_offset_db[rbw] + c
            for i, fz in enumerate(freqs):
                old = legacy_reference_dbm(ad_val, fz, lc, rbw)
                tc.assertIsNotNone(old, fz)
                diff = abs(new[i] - old)
                worst = max(worst, diff)
                if diff > 0.1:
                    tc.fail(f"f={fz}Hz rbw={rbw} ad={ad_val}: new={new[i]} old={old}")
    tc.assertLessEqual(worst, 0.1)


class TestToDbmMatchesLegacyOnAxis(unittest.TestCase):
    """to_dbm（等間隔の軸）を通しても旧アプリとの差が 0.1dB 以内。"""

    def test_axis(self):
        lc = parse_legacy_cal(build_legacy_bytes())
        conv = LevelConverter(PROFILE, make_cal(lc))
        adopted = make_adopted(0, 1_000_003, 12_000, Mode.SA, 30_000)
        ad = np.full(12_000, 1500, dtype=np.uint16)
        res: ConvertedLevels = conv.to_dbm(adopted, ad)
        for i, fz in enumerate(adopted.axis.freqs().tolist()):
            old = legacy_reference_dbm(1500, fz, lc, 30_000)
            self.assertLessEqual(abs(res.dbm[i] - old), 0.1, fz)


if __name__ == "__main__":
    unittest.main()
