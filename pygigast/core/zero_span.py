"""ゼロスパンの時間軸と流す表示（設計N §1）。純粋な計算だけ（Qt・スレッド・入出力を持たない）。

- 機器は時刻を返さないので、各点の時刻は推定: 掃引の開始時刻（要求を送った時刻）＋（前置きの点数＋点番号）×
  1点あたりの時間（採用値の point_time_s。機種のプロファイルの見積もり: 設計B §7.2）。前置きの点は機器が先に測る
- 横軸の位置はマイクロ秒の整数で持つ（マーカー・XY カーソルが周波数と同じ整数の仕組みのまま載る）
  - 1回の掃引（表示する長さ 0）: 表示する最初の点を 0
  - 流す表示: いちばん新しい点を 0、古いものほど負（古いものが左へ流れる）
- 掃引と掃引の間は途切れる（連続した時間の記録ではない）。描くための値（ビン）は途切れを NaN にして線を切る
- 区間の最大・最小・平均（パワー平均）は、間引かない全部の点から求める
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .view_units import ZERO_SPAN_LENGTHS_S

US = 1_000_000
# 流す表示のために持つ長さ（秒）。選べる一番長い表示（ZERO_SPAN_LENGTHS_S）。長さを切り替えてもすぐ全体が出る
KEEP_S = max(ZERO_SPAN_LENGTHS_S)
# 流す表示を描くときの横の区切り（ビン）の数の上限。画面の横の画素より多く、Web へ送る量は1本 16kB に収まる
MAX_BINS = 4000
# 間引いた点の印として渡すもの（描くときに控えめな色にする: 前置き不足・校正の範囲外）
DIM_FLAGS = ("transient", "out_of_cal_range")


@dataclass
class Piece:
    """1回の掃引の分（トレース1本）。t0 は表示する最初の点の時刻（UNIX 秒・推定）、dt は1点あたり（秒）。"""

    sweep_no: int
    t0: float
    dt: float
    level_db: np.ndarray  # float32。値なしは NaN（表示の単位でずらす前の dBm・dB）
    flags: dict = field(default_factory=dict)

    @property
    def t_end(self) -> float:
        return self.t0 + self.dt * (len(self.level_db) - 1)


class _Store:
    """トレース1本の点を古い順に続けて持つ（掃引ごとに足し、古い点を頭から捨てる）。表示のたびに掃引をつなぎ直さない
    （流す表示 60 秒は 30 万点になり、掃引ごとにつなぐと間に合わない）。"""

    def __init__(self) -> None:
        self.pieces: deque[Piece] = deque()
        self.t = np.zeros(0)
        self.level = np.zeros(0, dtype=np.float32)
        self.piece = np.zeros(0, dtype=np.int64)  # 足した順の通し番号
        self.index = np.zeros(0, dtype=np.int64)
        self.sweep_no = np.zeros(0, dtype=np.int64)
        self.flags: dict[str, np.ndarray] = {}
        self._serial = 0

    def add(self, p: Piece) -> None:
        n = p.level_db.size
        i = np.arange(n, dtype=np.int64)
        old = self.t.size
        self.t = np.concatenate((self.t, p.t0 + p.dt * i))
        self.level = np.concatenate((self.level, p.level_db))
        self.piece = np.concatenate((self.piece, np.full(n, self._serial, dtype=np.int64)))
        self.index = np.concatenate((self.index, i))
        self.sweep_no = np.concatenate((self.sweep_no, np.full(n, p.sweep_no, dtype=np.int64)))
        for name in set(self.flags) | set(p.flags):
            have = self.flags.get(name, np.zeros(old, dtype=bool))
            self.flags[name] = np.concatenate((have, p.flags.get(name, np.zeros(n, dtype=bool))))
        self._serial += 1
        self.pieces.append(p)

    def drop_before(self, t_oldest: float) -> None:
        while self.pieces and self.pieces[0].t_end < t_oldest:
            self.pieces.popleft()
        k = int(np.searchsorted(self.t, self.pieces[0].t0, side="left")) if self.pieces else self.t.size
        if k:
            self.t, self.level, self.piece = self.t[k:], self.level[k:], self.piece[k:]
            self.index, self.sweep_no = self.index[k:], self.sweep_no[k:]
            self.flags = {name: a[k:] for name, a in self.flags.items()}


@dataclass(frozen=True)
class Window:
    """表示する長さの中の点（古い順）。x_us は横軸の位置（いちばん新しい点が 0）、t は時刻（UNIX 秒・推定）。

    piece は点がどの掃引の分か（0 から数えた並びの番号）、index は掃引の中の点の番号、sweep_no は掃引の番号。
    """

    x_us: np.ndarray
    t: np.ndarray
    level_db: np.ndarray
    flags: dict
    piece: np.ndarray
    index: np.ndarray
    sweep_no: np.ndarray

    @property
    def empty(self) -> bool:
        return self.x_us.size == 0


def first_point_time(started_at: float, preroll_points: int, point_time_s: float) -> float:
    """表示する最初の点の時刻（推定）。前置きの点（設計C §5.6）は機器が先に測るので、その分だけ後ろ。"""
    return float(started_at) + max(0, int(preroll_points)) * float(point_time_s)


def offsets_us(points: int, point_time_s: float) -> np.ndarray:
    """1回の掃引の中の各点の位置（表示する最初の点から。マイクロ秒の整数）。"""
    return np.rint(np.arange(int(points), dtype=np.float64) * float(point_time_s) * US).astype(np.int64)


class History:
    """流す表示のための、トレースごとの直近 KEEP_S 秒の掃引（設計N §1）。

    掃引ごとに add_sweep で全部のトレースの分を足す。いちばん新しい点の時刻 t_ref が横軸の 0。
    """

    def __init__(self, keep_s: float = KEEP_S) -> None:
        self.keep_s = float(keep_s)
        self._stores: dict[int, _Store] = {}
        self._last_end: Optional[float] = None
        self.t_ref: Optional[float] = None

    def clear(self, trace: Optional[int] = None) -> None:
        """全部（trace を省いたとき）か、トレース1本の分を捨てる。"""
        if trace is None:
            self._stores.clear()
            self._last_end = None
            self.t_ref = None
        else:
            self._stores.pop(int(trace), None)

    def has(self, trace: int) -> bool:
        s = self._stores.get(int(trace))
        return bool(s and s.pieces)

    def add_sweep(self, sweep_no: int, t0: float, dt: float, per_trace: dict) -> float:
        """1回の掃引の全部のトレースの分を足す。per_trace は {番号: (level_db, flags)}。使った t0 を返す。

        推定の時刻が前の掃引と重なるとき（見積もりが実際の掃引より長いとき。シミュレータを速めたときなど）は、
        前の掃引の最後の点の 1点あとから並べる（時刻が逆戻りすると横軸の並びが崩れるため）。
        """
        if self._last_end is not None and t0 <= self._last_end:
            t0 = self._last_end + dt
        n = 0
        for trace, (level, flags) in per_trace.items():
            lv = np.asarray(level, dtype=np.float32).copy()
            n = max(n, lv.size)
            fl = {k: np.asarray(v, dtype=bool).copy() for k, v in (flags or {}).items()
                  if np.shape(v) == lv.shape}
            self._stores.setdefault(int(trace), _Store()).add(Piece(int(sweep_no), float(t0), float(dt), lv, fl))
        if n:
            self._last_end = t0 + dt * (n - 1)
            self.t_ref = self._last_end
            self._prune()
        return t0

    def _prune(self) -> None:
        if self.t_ref is None:
            return
        oldest = self.t_ref - self.keep_s
        for s in self._stores.values():
            s.drop_before(oldest)

    def window(self, trace: int, length_s: float) -> Optional[Window]:
        """表示する長さの中の点。無ければ None。"""
        s = self._stores.get(int(trace))
        if s is None or not s.pieces or self.t_ref is None:
            return None
        x_all = np.rint((s.t - self.t_ref) * US).astype(np.int64)
        k = int(np.searchsorted(x_all, -float(length_s) * US, side="left"))
        if k >= x_all.size:
            return None
        return Window(x_us=x_all[k:], t=s.t[k:], level_db=s.level[k:], flags={n: a[k:] for n, a in s.flags.items()},
                      piece=s.piece[k:], index=s.index[k:], sweep_no=s.sweep_no[k:])

    def latest(self, trace: int) -> Optional[Piece]:
        s = self._stores.get(int(trace))
        return s.pieces[-1] if s and s.pieces else None


def bins(win: Window, length_s: float, point_time_s: float, use_min: bool = False,
         max_bins: int = MAX_BINS) -> tuple[float, float, np.ndarray, dict]:
    """流す表示を描く値（設計F §5.5: 区切りごとに最大値、最小保持のトレースは最小値）。

    戻り値: (最初の区切りの位置 μs, 区切りの幅 μs, 値 float32, 印 {名前: bool})。区切りは −長さ〜0 を等しい幅に分け、
    値は区切りの真ん中に置く。点の無い区切り（掃引と掃引の間）は NaN（線を切って空白にする）。区切りの幅は
    1点あたりの時間より広くして、1回の掃引の中に点の無い区切りを作らない。隣り合う区切りが別の掃引の点なら、
    後ろの区切りを NaN にする（途切れが区切りより短いときも、つながっているように描かない）。
    """
    span = float(length_s) * US
    step = max(span / max_bins, float(point_time_s) * US * 1.0001, 1.0)
    n = max(1, int(math.ceil(span / step)))
    start = -span
    out = np.full(n, np.nan)
    flags: dict = {}
    if win.x_us.size == 0:
        return start + step / 2, step, out.astype(np.float32), flags
    # 点は時間の順なので、区切りの番号も順に並ぶ。区切りごとの塊に分けて最大（最小）をとる
    k = np.clip(np.floor((win.x_us.astype(np.float64) - start) / step).astype(np.int64), 0, n - 1)
    heads = np.flatnonzero(np.r_[True, k[1:] != k[:-1]])
    tails = np.r_[heads[1:], k.size] - 1
    fill = np.inf if use_min else -np.inf
    lv = np.where(np.isfinite(win.level_db), win.level_db.astype(np.float64), fill)
    best = (np.minimum if use_min else np.maximum).reduceat(lv, heads)
    out[k[heads]] = np.where(np.isfinite(best), best, np.nan)
    has = np.zeros(n, dtype=bool)
    has[k[heads]] = True
    first_piece = np.full(n, -1, dtype=np.int64)
    last_piece = np.full(n, -1, dtype=np.int64)
    first_piece[k[heads]] = win.piece[heads]
    last_piece[k[heads]] = win.piece[tails]
    joined = has[:-1] & has[1:] & (last_piece[:-1] != first_piece[1:])
    out[1:][joined] = np.nan
    for name in DIM_FLAGS:
        a = win.flags.get(name)
        if a is not None and a.any():
            f = np.zeros(n, dtype=bool)
            f[k[heads]] = np.logical_or.reduceat(np.asarray(a, dtype=bool), heads)
            flags[name] = f
    return start + step / 2, step, out.astype(np.float32), flags


def interval_stats(x_us: np.ndarray, level_db: np.ndarray, usable: np.ndarray, x0: int, x1: int) -> Optional[dict]:
    """区間 [x0, x1] の最大・最小・平均（パワー平均。dB を電力に直して平均し、dB に戻す: 設計N §1）。

    usable は数える点（値のある・印で外さない点）。点が無ければ None。最大・最小は位置（μs）も返す
    （同じ値が複数あれば古い方）。
    """
    lo, hi = (x0, x1) if x0 <= x1 else (x1, x0)
    m = usable & (x_us >= lo) & (x_us <= hi) & np.isfinite(level_db)
    if not m.any():
        return None
    xs = x_us[m]
    lv = level_db[m].astype(np.float64)
    i_max, i_min = int(np.argmax(lv)), int(np.argmin(lv))
    avg = 10.0 * math.log10(float(np.mean(np.power(10.0, lv / 10.0))))
    return {"max": float(lv[i_max]), "max_us": int(xs[i_max]), "min": float(lv[i_min]), "min_us": int(xs[i_min]),
            "avg": avg, "points": int(m.sum())}


def x_range_us(length_s: float, points: int, point_time_s: float) -> tuple[int, int]:
    """横軸の範囲（μs）。1回の掃引は 0〜最後の点、流す表示は −長さ〜0。"""
    if not length_s:
        return 0, int(offsets_us(max(1, points), point_time_s)[-1]) if points > 1 else 0
    return -int(round(float(length_s) * US)), 0
