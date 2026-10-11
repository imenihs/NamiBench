"""ウォーターフォールと密度表示のデータ（設計P §3・§4・§8・§9.2・§9.3）。画面には描かない（描くのは Qt と Web）。

- 行（Row）: 1掃引（またはまとめた数掃引）の値。周波数は行ごとに持つ（条件が途中で変わった記録は、各行をその行の
  周波数の位置に描く: P §3）。値は測った単位の dB（電力は dBm・比は dB・未校正は dB（未校正））で持ち、表示の単位
  （dBμV・外部補正: 設計O）へは画面へ出すときにずらす。列は多くても WF_MAX_COLUMNS で、それより点の多い掃引は
  列ごとに最大値で間引く（ピークを消さない: P §8）
- 行のまとめ（select_rows）: 画面の行数より掃引が多いときは、続く掃引を最大値でまとめる（P §8・H §3.1 の概観と同じ
  まとめ方）。条件の違う掃引どうしはまとめない
- 色の範囲の自動（AutoRange）: ノイズフロア（行の値の中央値）から最大値まで。数秒ごとにゆっくり追う（行ごとに色が
  変わってちらつかないように: P §9.2）
- 密度（Density）: 升目は 横 = 周波数の列（多くても DENSITY_MAX_COLUMNS）・縦 = 0.5dB 刻み（P §9.3 の仮の値。
  縦軸の 10 目盛が 100dB を超えるときは升の数を DENSITY_MAX_BINS に保つよう広げる）。
  掃引ごとに各升目へ来た回数を足し、古い掃引の重みは指数的に減らす（N 回で半分）

Qt を import しない。
"""
from __future__ import annotations

import collections
import math
from dataclasses import dataclass, field, replace
from typing import Iterable, Optional, Sequence

import numpy as np

# 行に持つ列の上限（12000 点の GigaSt の掃引を 4096 列に。20 万点の RTL-SDR も同じ）
WF_MAX_COLUMNS = 4096
# 色の範囲の自動: 追う速さ（時定数の秒）・見せる範囲を変える最短の間隔（秒）・範囲の最小の幅・丸める刻み（dB）
AUTO_TAU_S = 3.0
AUTO_COMMIT_S = 2.0
AUTO_MIN_SPAN_DB = 10.0
AUTO_STEP_DB = 0.5
# 密度: 縦の刻み（dB）・横の列の上限・見せる下限（1 回の掃引の 1% に満たない重みは出さない）
DENSITY_BIN_DB = 0.5
# dB の縦軸の密度の升の数の上限（10dB/div の 0.5dB 刻みと同じ 200 升）。20・40dB/div では升目の高さを 1・2dB に
# 広げる（縦の画素より細かい升を作っても見えず、画面へ送る絵だけが 2・4 倍に膨らむため）
DENSITY_MAX_BINS = 200
# |Γ|・SWR（縦軸が dB でない: 設計R §3）の密度の升目の高さは、縦軸の1目盛のこの数分の1（10 目盛で 200 升）
DENSITY_LINEAR_BINS_PER_DIV = 20
DENSITY_MAX_COLUMNS = 1024
DENSITY_MIN_HITS = 0.01
# 密度を作り直すときに読み直す行（重みが 1/256 になるまで = 半分になる回数の 8 倍）
DENSITY_REPLAY_HALVES = 8


@dataclass
class Row:
    t: float  # 行の最初の掃引の開始時刻（UNIX 秒）
    t_end: float  # 行の最後の掃引の開始時刻（1掃引の行は t と同じ）
    start_hz: int  # 列 0 の周波数（ゼロスパンは点の番号 0）
    step_hz: int  # 列の間隔（間引いたら元の Step × 間引いた数。ゼロスパンは点の数）
    values: np.ndarray  # 列ごとの値（測った単位の dB。値の無い列は NaN）。float32
    unit: str  # 値の単位（dBm・dB・dB（未校正））
    seg: int  # 条件が同じ並びの番号（条件が変わると変わる）
    cond: dict  # 境目に出す条件（start_hz・stop_hz・rbw_hz・mode）
    sweeps: int = 1  # まとめた掃引の数
    pos: Optional[tuple[int, int]] = None  # 記録のファイルの中の (区間, 掃引の番号)。ライブの行は None
    zero_span: bool = False  # ゼロスパン（横軸は点の番号）
    extra: dict = field(default_factory=dict)

    @property
    def columns(self) -> int:
        return int(self.values.shape[0])

    def head(self, shift_db: float) -> dict:
        """画面へ送る見出し（値の配列は別に送る: Web はバイナリ）。"""
        return {"t": self.t, "t_end": self.t_end, "start_hz": int(self.start_hz), "step_hz": int(self.step_hz),
                "n": self.columns, "seg": int(self.seg), "sweeps": int(self.sweeps),
                "pos": list(self.pos) if self.pos is not None else None, "cond": dict(self.cond),
                "zero_span": self.zero_span}


def decimate_max(values: np.ndarray, max_cols: int = WF_MAX_COLUMNS) -> tuple[np.ndarray, int]:
    """点が max_cols より多ければ、続く k 点ごとの最大値（値の無い点は除く・全部無ければ NaN）にする。(値, k)。"""
    v = np.asarray(values, dtype=np.float32)
    n = int(v.shape[0])
    if n <= max_cols:
        return v.copy(), 1
    k = -(-n // max_cols)
    pad = (-n) % k
    if pad:
        v = np.concatenate([v, np.full(pad, np.nan, dtype=np.float32)])
    blocks = v.reshape(-1, k)
    with np.errstate(invalid="ignore"):
        out = np.fmax.reduce(blocks, axis=1)
    return out.astype(np.float32), k


def row_from_values(t: float, freqs_start_hz: int, step_hz: int, values_db: np.ndarray, *, unit: str, seg: int,
                    cond: dict, pos: Optional[tuple[int, int]] = None, sweeps: int = 1,
                    t_end: Optional[float] = None) -> Row:
    """1掃引の値（表示する範囲の点だけ）から行を作る。step_hz 0 はゼロスパン（横軸は点の番号）。"""
    vals, k = decimate_max(values_db)
    zero = int(step_hz) == 0
    return Row(t=float(t), t_end=float(t if t_end is None else t_end), start_hz=0 if zero else int(freqs_start_hz),
               step_hz=k if zero else int(step_hz) * k, values=vals, unit=unit, seg=int(seg), cond=dict(cond),
               sweeps=int(sweeps), pos=pos, zero_span=zero)


def merge(rows: Sequence[Row]) -> Row:
    """同じ条件の続く行を1行に（列ごとの最大値）。時刻は最初の行の開始から最後の行の開始まで。"""
    first = rows[0]
    if len(rows) == 1:
        return first
    vals = np.stack([r.values for r in rows])
    with np.errstate(invalid="ignore"):
        out = np.fmax.reduce(vals, axis=0).astype(np.float32)
    return replace(first, t=min(r.t for r in rows), t_end=max(r.t_end for r in rows), values=out,
                   sweeps=sum(r.sweeps for r in rows), pos=first.pos)


def select_rows(rows: Sequence[Row], max_rows: int) -> list[Row]:
    """古い順の行を max_rows 行以内にする（続く行を最大値でまとめる。条件の違う行はまとめない）。"""
    rows = list(rows)
    if max_rows <= 0:
        return []
    if len(rows) <= max_rows:
        return rows
    k = -(-len(rows) // max_rows)
    while True:
        out: list[Row] = []
        group: list[Row] = []
        for r in rows:
            if group and (len(group) >= k or r.seg != group[0].seg or r.columns != group[0].columns
                          or r.step_hz != group[0].step_hz or r.start_hz != group[0].start_hz):
                out.append(merge(group))
                group = []
            group.append(r)
        if group:
            out.append(merge(group))
        if len(out) <= max_rows or k >= len(rows):
            return out[-max_rows:]
        k += max(1, k // 2)


class History:
    """画面に持つ行（古い順）。上限を超えた古い行から捨てる（P §3: 上限を超えた行は記録のファイルから読み直す）。"""

    def __init__(self, limit: int) -> None:
        self._rows: collections.deque[Row] = collections.deque(maxlen=int(limit))

    @property
    def limit(self) -> int:
        return int(self._rows.maxlen or 0)

    def set_limit(self, limit: int) -> None:
        if int(limit) != self.limit:
            self._rows = collections.deque(self._rows, maxlen=int(limit))

    def add(self, row: Row) -> None:
        self._rows.append(row)

    def clear(self) -> None:
        self._rows.clear()

    def __len__(self) -> int:
        return len(self._rows)

    def rows(self) -> list[Row]:
        return list(self._rows)

    def first_time(self) -> Optional[float]:
        return self._rows[0].t if self._rows else None

    def latest(self) -> Optional[Row]:
        return self._rows[-1] if self._rows else None

    def between(self, t_lo: Optional[float], t_hi: Optional[float]) -> list[Row]:
        return [r for r in self._rows if (t_lo is None or r.t_end >= t_lo) and (t_hi is None or r.t <= t_hi)]

    def find(self, t: float) -> Optional[Row]:
        """時刻 t を含む行（無ければ最も近い行）。"""
        best = None
        for r in self._rows:
            if r.t <= t <= r.t_end:
                return r
            d = min(abs(r.t - t), abs(r.t_end - t))
            if best is None or d < best[0]:
                best = (d, r)
        return best[1] if best else None


def finite_stats(values: np.ndarray) -> Optional[tuple[float, float]]:
    """値の無い点を除いた (中央値, 最大値)。1点も無ければ None。"""
    v = np.asarray(values, dtype=np.float64)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return None
    return float(np.median(v)), float(v.max())


def _round_step(x: float) -> float:
    return round(x / AUTO_STEP_DB) * AUTO_STEP_DB


class AutoRange:
    """色の範囲の自動（P §9.2）。ノイズフロア（中央値）〜最大値を時定数 AUTO_TAU_S で追い、見せる範囲は
    AUTO_COMMIT_S 秒より短い間隔では変えない（最初の1行はすぐ決める）。"""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._lo: Optional[float] = None
        self._hi: Optional[float] = None
        self._t: Optional[float] = None
        self._commit_t: Optional[float] = None
        self.range: Optional[tuple[float, float]] = None

    def shift(self, delta_db: float) -> None:
        """追っている範囲を delta_db だけ動かす（行の値が同じだけ動いたとき: 基準の値を直した 設計E §6.7）。"""
        if self._lo is not None:
            self._lo += delta_db
            self._hi += delta_db
        if self.range is not None:
            self.range = (round(self.range[0] + delta_db, 2), round(self.range[1] + delta_db, 2))

    def feed(self, values_display: np.ndarray, now: float) -> bool:
        """1行の値（表示の単位）を入れる。見せる範囲が変わったら True。"""
        st = finite_stats(values_display)
        if st is None:
            return False
        lo_t, hi_t = st
        if hi_t - lo_t < AUTO_MIN_SPAN_DB:
            hi_t = lo_t + AUTO_MIN_SPAN_DB
        if self._lo is None or self._t is None:
            self._lo, self._hi = lo_t, hi_t
        else:
            a = 1.0 - math.exp(-max(0.0, now - self._t) / AUTO_TAU_S)
            self._lo += a * (lo_t - self._lo)
            self._hi += a * (hi_t - self._hi)
        self._t = now
        if self.range is not None and self._commit_t is not None and now - self._commit_t < AUTO_COMMIT_S:
            return False
        lo, hi = _round_step(self._lo), _round_step(self._hi)
        if hi - lo < AUTO_MIN_SPAN_DB:
            hi = lo + AUTO_MIN_SPAN_DB
        new = (lo, hi)
        self._commit_t = now
        if new != self.range:
            self.range = new
            return True
        return False


class Density:
    """密度（パーシステンス: P §4・§9.3）。key が変わったら作り直す（呼ぶ側が同じ条件の行を入れ直す）。"""

    def __init__(self) -> None:
        self.key: Optional[tuple] = None
        self._acc: Optional[np.ndarray] = None
        self._wsum = 0.0
        self._decay = 0.5
        self._cols_of: Optional[np.ndarray] = None
        self._per_col: Optional[np.ndarray] = None
        self.n_cols = 0
        self.bins = 0
        self.level_lo = 0.0
        self.level_hi = 0.0
        self.bin = DENSITY_BIN_DB
        self.added = 0

    def setup(self, key: tuple, points: int, level_lo: float, level_hi: float, half: int,
              bin_size: float = DENSITY_BIN_DB) -> bool:
        """条件（列の数を決める点の数・縦の範囲・半分になる回数・升目の高さ）を決める。変わって作り直したら True。
        升目の高さは表示の値の単位（dB 系は DENSITY_BIN_DB、|Γ|・SWR は1目盛 / DENSITY_LINEAR_BINS_PER_DIV）。"""
        if key == self.key and self._acc is not None:
            return False
        self.key = key
        n = max(1, int(points))
        self.n_cols = min(n, DENSITY_MAX_COLUMNS)
        self._cols_of = (np.arange(n, dtype=np.int64) * self.n_cols) // n
        self._per_col = np.bincount(self._cols_of, minlength=self.n_cols).astype(np.float64)
        self.level_lo, self.level_hi = float(level_lo), float(level_hi)
        self.bin = float(bin_size) if bin_size and bin_size > 0 else DENSITY_BIN_DB
        self.bins = max(1, int(round((self.level_hi - self.level_lo) / self.bin)))
        self._acc = np.zeros((self.bins, self.n_cols), dtype=np.float64)
        self._wsum = 0.0
        self._decay = 0.5 ** (1.0 / max(1, int(half)))
        self.added = 0
        return True

    def add(self, values_display: np.ndarray) -> None:
        """1掃引の値（表示の単位・点の数は setup の points）を足す。"""
        if self._acc is None or self._cols_of is None or self._per_col is None:
            return
        v = np.asarray(values_display, dtype=np.float64)
        if v.shape[0] != self._cols_of.shape[0]:
            return
        with np.errstate(invalid="ignore"):
            b = np.floor((v - self.level_lo) / self.bin)
        ok = np.isfinite(b) & (b >= 0) & (b < self.bins)
        idx = b[ok].astype(np.int64) * self.n_cols + self._cols_of[ok]
        hits = np.bincount(idx, minlength=self.bins * self.n_cols).reshape(self.bins, self.n_cols)
        self._acc *= self._decay
        self._acc += hits / self._per_col
        self._wsum = self._wsum * self._decay + 1.0
        self.added += 1

    def image(self) -> np.ndarray:
        """[縦, 横] の uint8（0 = 来ていない・1〜255 = 来た割合の対数の段）。0 行目が上（高いレベル）。"""
        if self._acc is None or self._wsum <= 0:
            return np.zeros((max(1, self.bins), max(1, self.n_cols)), dtype=np.uint8)
        p = self._acc / self._wsum
        g = np.log10(1.0 + 99.0 * np.clip(p, 0.0, 1.0)) / 2.0
        img = np.where(self._acc >= DENSITY_MIN_HITS, 1 + np.round(g * 254.0), 0).astype(np.uint8)
        return img[::-1].copy()


def replay_rows(rows: Iterable[Row], half: int) -> list[Row]:
    """密度を作り直すときに入れ直す行（新しい方から、重みが 1/256 になるまで）。古い順で返す。"""
    rows = list(rows)
    n = DENSITY_REPLAY_HALVES * max(1, int(half))
    return rows[-n:]


def sweep_interval_s(times: Sequence[float]) -> Optional[float]:
    """続く掃引の開始時刻の間隔の中央値（何秒分の密度か: P §4）。2つ未満なら None。"""
    t = np.asarray(times, dtype=np.float64)
    if t.size < 2:
        return None
    d = np.diff(np.sort(t))
    d = d[d > 0]
    return float(np.median(d)) if d.size else None
