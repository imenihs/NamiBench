"""NF 測定の計算（設計M §1）。Y 係数法・ENR（1つの値か周波数ごとの表）・増幅度の補正。純粋な計算だけ。

- HOT と COLD はそれぞれパワー平均してから計算する（平均の後に NF を出す。NF の値を平均しない）。平均はトレースの
  「平均」が HOT・COLD の電力で取り、ここの evaluate で NF にする（core/trace/trace.py）
- HOT−COLD の dB の差に、増幅度の倍率 10^(amp_raw/200) を掛ける（旧 NF_Amp と同じ意味: 設計D §5.2）
- Y = 10^(補正した差/10)、NF = ENR − 10·log10(Y − 1)（dB。一般に使われる Y 係数法の式）
- 補正した差が 0.5dB 未満の点には印を付ける（値は出す）。Y ≤ 1 の点は式が値を持たないので値なし（NaN）
- ENR の表の間は dB で線形補間する（周波数は Hz の整数の差で重みを作る: core/trace/ops.interp_db）。
  表の外の周波数は近い端の値を使い、その点に印を付ける（外へ延ばさない）
- トレースの値の持ち方: NF の dB を、比と同じ 20·log10 で読める振幅の形 10^(NF/20) にする（core/trace/ops.to_db・
  マーカー・CSV がそのまま dB で読める）
- HOT・COLD の電力は補正を当てない値（設計D §3: NF では周波数特性・全体のレベルの補正を当てない。差だけを使う）
"""
from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..trace.ops import interp_db
from ..units import ParseError, format_freq, parse_db, parse_freq_hz

# HOT と COLD の差がこれより小さい点は NF が大きく不確かになる（設計M §1）。印を付ける
SMALL_DIFFERENCE_DB = 0.5
# 点ごとの印の名前（Frame.flags。マーカーの読み値の注意にもなる: 設計G §3.3）
FLAG_SMALL_DIFFERENCE = "nf_small_difference"
FLAG_ENR_OUTSIDE = "enr_outside_table"
# ENR として受け付ける範囲（dB。ノイズ源の ENR は数〜30dB 程度。打ち間違いで画面が使えなくなるのを防ぐ。設計に値は無い）
ENR_RANGE_DB = (0.0, 50.0)
# ENR の表の大きさの上限（打ち間違えて別の大きなファイルを読んだときに止める。設計に値は無い）
MAX_TABLE_ROWS = 10000
MAX_TABLE_CHARS = 2_000_000

# 表の見出しの周波数の単位（1列目の名前の中の語）→ 単位を書いていない数の桁（10 の何乗 Hz）
_HEADER_UNITS = (("ghz", 9), ("mhz", 6), ("khz", 3), ("hz", 0))
_SPLIT = re.compile(r"[,\t;]+|\s+")


class EnrTableError(ValueError):
    """ENR の表が読めない。line は 1 から数えた行（ファイル全体の問題なら 0）、reason は理由のコード。"""

    def __init__(self, line: int, reason: str) -> None:
        super().__init__(f"{line}: {reason}")
        self.line = line
        self.reason = reason


def amp_factor(amp_raw: Optional[float]) -> float:
    """増幅度の倍率（旧 NF_Amp の値 → 10^(値/200)）。校正していなければ 1。"""
    return 1.0 if amp_raw is None else 10.0 ** (float(amp_raw) / 200.0)


def y_db(hot_w: np.ndarray, cold_w: np.ndarray, amp_raw: Optional[float] = None) -> np.ndarray:
    """補正した HOT−COLD（dB）。電力が 0 か値なしの点は NaN。"""
    h = np.asarray(hot_w, dtype=np.float64)
    c = np.asarray(cold_w, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        d = 10.0 * np.log10(h / c)
    d = np.where(np.isfinite(d), d, np.nan)
    return d * amp_factor(amp_raw)


def nf_from_y(ydb: np.ndarray, enr_db) -> np.ndarray:
    """NF = ENR − 10·log10(Y − 1)。Y ≤ 1（差が 0 以下）と値なしの点は NaN。"""
    y = np.asarray(ydb, dtype=np.float64)
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        lin = np.power(10.0, y / 10.0) - 1.0
        nf = np.asarray(enr_db, dtype=np.float64) - 10.0 * np.log10(lin)
    return np.where(np.isfinite(nf) & (lin > 0), nf, np.nan)


def to_values(nf_db: np.ndarray) -> np.ndarray:
    """NF の dB → トレースに持つ値（20·log10 で dB に戻る振幅の形）。NaN は NaN のまま。"""
    with np.errstate(over="ignore", invalid="ignore"):
        return np.power(10.0, np.asarray(nf_db, dtype=np.float64) / 20.0)


def values_to_db(values: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return 20.0 * np.log10(np.abs(np.asarray(values, dtype=np.float64)))


# ---------------------------------------------------------------------------- ENR の表


@dataclass(frozen=True)
class EnrTable:
    """周波数ごとの ENR（周波数の昇順。同じ周波数は無い）。name は読み込んだファイルの名前（画面と保存に出す）。"""

    freqs_hz: tuple[int, ...]
    enr_db: tuple[float, ...]
    name: str = ""

    def __post_init__(self) -> None:
        if not self.freqs_hz or len(self.freqs_hz) != len(self.enr_db):
            raise ValueError("表の行が無いか、周波数と ENR の数が違う")
        if any(b <= a for a, b in zip(self.freqs_hz, self.freqs_hz[1:])):
            raise ValueError("周波数は昇順で、同じ周波数を持たない")

    @property
    def start_hz(self) -> int:
        return self.freqs_hz[0]

    @property
    def stop_hz(self) -> int:
        return self.freqs_hz[-1]

    def at(self, freqs_hz: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """各点の ENR（dB）と「表の範囲外」の印。表の外は近い端の値（延ばさない）。"""
        f = np.asarray(freqs_hz)
        if f.dtype.kind not in "iu":
            raise TypeError("周波数は整数（Hz）の配列で渡す")
        f = f.astype(np.int64)
        lo, hi = self.start_hz, self.stop_hz
        outside = (f < lo) | (f > hi)
        src_f = np.asarray(self.freqs_hz, dtype=np.int64)
        src_v = np.asarray(self.enr_db, dtype=np.float64)
        return interp_db(src_f, src_v, np.clip(f, lo, hi)), outside

    def to_dict(self) -> dict:
        return {"name": self.name, "rows": [[int(f), float(v)] for f, v in zip(self.freqs_hz, self.enr_db)]}

    @classmethod
    def from_dict(cls, d: dict) -> "EnrTable":
        rows = d["rows"]
        return cls(tuple(int(r[0]) for r in rows), tuple(float(r[1]) for r in rows), str(d.get("name") or ""))


def _header_exp(cell: str) -> Optional[int]:
    c = cell.lower()
    for word, exp in _HEADER_UNITS:
        if word in c:
            return exp
    return None


def _cells(line: str) -> list[str]:
    if "," in line or "\t" in line or ";" in line:
        cells = next(csv.reader(io.StringIO(line), delimiter="," if "," in line else ("\t" if "\t" in line else ";")))
    else:
        cells = _SPLIT.split(line.strip())
    return [c.strip() for c in cells if c.strip() != ""]


def parse_enr_csv(text: str, name: str = "") -> EnrTable:
    """ENR の表の CSV を読む。

    - 1行に「周波数, ENR」。区切りはカンマ・タブ・セミコロン・空白。空の行と # で始まる行は読み飛ばす
    - 最初の、数で始まらない行は見出し。1列目の名前に GHz・MHz・kHz・Hz があれば、単位を書いていない周波数の単位にする
      （無ければ MHz: アプリの周波数の欄と同じ）。周波数の数には単位を付けてもよい（1.5G・1500M・1500MHz）
    - ENR は dB の小数。3列目からは読まない（不確かさの列などを持つ表がある）
    - 周波数の順は問わない（並べ替える）。同じ周波数が2回ある・数が読めない・ENR が範囲外なら EnrTableError
    """
    if len(text) > MAX_TABLE_CHARS:
        raise EnrTableError(0, "too_large")
    exp = 6
    seen_header = False
    rows: dict[int, float] = {}
    first_line: dict[int, int] = {}
    for i, raw in enumerate(text.lstrip("﻿").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        cells = _cells(line)
        if not cells:
            continue
        if not rows and not seen_header and not re.match(r"^[+\-−]?(\d|\.\d)", cells[0]):
            seen_header = True
            h = _header_exp(cells[0])
            if h is not None:
                exp = h
            continue
        if len(cells) < 2:
            raise EnrTableError(i, "columns")
        try:
            f = parse_freq_hz(cells[0], default_exp=exp)
        except ParseError:
            raise EnrTableError(i, "frequency") from None
        try:
            v = parse_db(cells[1])
        except ParseError:
            raise EnrTableError(i, "enr") from None
        if f < 0:
            raise EnrTableError(i, "frequency")
        if not math.isfinite(v) or not (ENR_RANGE_DB[0] <= v <= ENR_RANGE_DB[1]):
            raise EnrTableError(i, "enr_range")
        if f in rows:
            raise EnrTableError(i, "duplicate")
        rows[f] = float(v)
        first_line[f] = i
        if len(rows) > MAX_TABLE_ROWS:
            raise EnrTableError(i, "too_large")
    if not rows:
        raise EnrTableError(0, "empty")
    fs = sorted(rows)
    return EnrTable(tuple(fs), tuple(rows[f] for f in fs), name)


def table_summary(t: EnrTable) -> dict:
    """画面に出す表の要約（名前・範囲・行の数）。"""
    return {"name": t.name, "rows": len(t.freqs_hz), "start_hz": t.start_hz, "stop_hz": t.stop_hz,
            "start_text": format_freq(t.start_hz), "stop_text": format_freq(t.stop_hz)}


# ---------------------------------------------------------------------------- いま使う ENR


ENR_VALUE = "value"  # 利用者が入れた1つの値
ENR_TABLE = "table"  # 読み込んだ表
ENR_CALIBRATION = "calibration"  # 利用者が入れていないので校正データの値（旧校正ファイルの ENR を引き継いだもの）


@dataclass(frozen=True)
class Enr:
    """NF の計算に使う ENR（どこから来たか・値か表）。"""

    kind: str
    db: Optional[float] = None
    table: Optional[EnrTable] = None

    def at(self, freqs_hz: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        f = np.asarray(freqs_hz)
        if self.kind == ENR_TABLE and self.table is not None:
            return self.table.at(f)
        return np.full(f.shape, float(self.db), dtype=np.float64), np.zeros(f.shape, dtype=bool)

    def record(self) -> dict:
        """保存・書き出しに書く形（あとで NF を計算し直せるように: 設計H §3）。"""
        if self.kind == ENR_TABLE and self.table is not None:
            return {"kind": ENR_TABLE, "table": self.table.to_dict()}
        return {"kind": self.kind, "db": self.db}


# ---------------------------------------------------------------------------- 1回分（トレースへ渡す）


@dataclass
class NfParts:
    """1回の掃引の HOT・COLD の電力（W）と、NF にするための値（Frame.nf）。

    トレースは「平均」のとき HOT と COLD をそれぞれパワー平均し、evaluate で NF にする（設計M §1）。
    enr_db が None（ENR が決まっていない）のときは NF を出せないので全部の点が値なし。
    """

    hot_w: np.ndarray
    cold_w: np.ndarray
    enr_db: Optional[np.ndarray]
    amp_raw: Optional[float] = None
    enr_outside: Optional[np.ndarray] = None

    def evaluate(self, hot_w: np.ndarray, cold_w: np.ndarray) -> tuple[np.ndarray, dict[str, np.ndarray]]:
        """HOT・COLD（平均したもの）から、トレースの値と点ごとの印（差が小さい・ENR の表の外）。"""
        y = y_db(hot_w, cold_w, self.amp_raw)
        small = ~(y >= SMALL_DIFFERENCE_DB)  # NaN（電力が 0 の点）も小さいとみなす
        if self.enr_db is None:
            nf = np.full(y.shape, np.nan)
        else:
            nf = nf_from_y(y, self.enr_db)
        flags = {FLAG_SMALL_DIFFERENCE: small}
        if self.enr_outside is not None:
            flags[FLAG_ENR_OUTSIDE] = np.asarray(self.enr_outside, dtype=bool).copy()
        return to_values(nf), flags
