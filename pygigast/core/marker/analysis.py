"""マーカーが読むトレースの見方と、ピーク・帯域幅・しきい値の探索（設計G §3.2・§3.5）。

- 入力は1本のトレースの周波数軸とリニアの値（と量の種類）、表示範囲 Start〜Stop だけ
- マーカーは表示範囲の中の点にだけ載る（補間しない: 設計G §3.1）。
  補間するのは N dB 帯域幅・しきい値の交点の周波数だけで、Hz の int に丸めて返す
- 値なしの点（ノーマライズの基準が無い点・dB にできない値）は読まない・探さない（設計E §6.4）
- 純粋な計算だけ。Qt・スレッド・入出力を持たない
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from ..types import FreqAxis, Frame, Quantity

# 探す対象から外す点の印（設計G §3.2「前置き不足・範囲外」、設計E §6.4「基準の無い点」）
EXCLUDE_FLAGS = ("transient", "out_of_cal_range", "no_reference")
# 値そのものが無い点の印（設計E §6.4: マーカーは「値なし」と出す）
NO_VALUE_FLAGS = ("no_reference",)

UNIT_DBM = "dBm"
UNIT_DB = "dB"

# 次のピークの既定の盛り上がり（設計G §3.2: 周りより 3dB 以上。値は設定で変えられる）
DEFAULT_EXCURSION_DB = 3.0

# 探索の結果のコード（画面の文言は画面側が決める: U-22）
OK = "ok"
EDGE = "edge"  # 表示範囲の端まで交点が無かった
NO_VALUE_REGION = "no_value_region"  # 値なしの区間に達した（設計E §6.4）
NO_MARKER_VALUE = "no_marker_value"  # 起点の点に値が無い
NOT_IN_REGION = "not_in_region"  # 起点がしきい値の指定の側にない

SIDE_BELOW = "below"  # しきい値以下の範囲（例: S11 で SWR 2 以下）
SIDE_ABOVE = "above"  # しきい値以上の範囲


@dataclass(frozen=True)
class TraceView:
    """マーカーが読む1本のトレース。値は dB（電力は dBm、比は dB）に直して持つ。

    freqs は各点の周波数（int64）。level_db は値なしの点を NaN にしたもの。
    lo〜hi（hi は含まない）が表示範囲 Start〜Stop に入る点の番号。
    searchable はピークを探してよい点（印の無い・値のある点）。
    notes はトレース全体に付く読み値の注意のコード（RBW 15kHz の1回の掃引・基準が粗いなど:
    設計G §3.3）。作る側が決めて渡し、ここでは読み値に添えるだけ。
    """

    trace: int
    freqs: np.ndarray
    level_db: np.ndarray
    unit: str
    quantity: Quantity
    start_hz: int
    stop_hz: int
    lo: int
    hi: int
    searchable: np.ndarray
    flags: dict = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    @classmethod
    def build(
        cls,
        trace: int,
        axis: FreqAxis,
        values: np.ndarray,
        quantity: Quantity,
        display_start_hz: int,
        display_stop_hz: int,
        flags: Optional[dict] = None,
        notes: tuple[str, ...] = (),
    ) -> "TraceView":
        """周波数軸とリニアの値から作る。

        電力は W → dBm（10·log10(W) + 30: 設計D §5.3 の逆）、比はリニアの振幅比 → dB（20·log10|v|）。
        複素数は振幅で読む（VNA の形式は設計R §3 で別に決める）。
        """
        for name, v in (("display_start_hz", display_start_hz), ("display_stop_hz", display_stop_hz)):
            if isinstance(v, bool) or not isinstance(v, int):
                raise TypeError(f"{name} は int（Hz）で渡す")
        freqs = axis.freqs()
        vals = np.asarray(values)
        if vals.shape != (axis.points,):
            raise ValueError("values の点数が軸と合わない")
        mag = np.abs(vals).astype(np.float64)
        with np.errstate(divide="ignore", invalid="ignore"):
            if quantity == Quantity.POWER:
                level = 10.0 * np.log10(mag) + 30.0
                unit = UNIT_DBM
            else:
                level = 20.0 * np.log10(mag)
                unit = UNIT_DB
        # 0 W・NaN など dB にできない値は値なしとして扱う（数値を作らない）
        level = np.where(np.isfinite(level), level, np.nan)
        flags = dict(flags or {})
        exclude = np.zeros(axis.points, dtype=bool)
        for name in EXCLUDE_FLAGS:
            if name in flags:
                exclude |= np.asarray(flags[name], dtype=bool)
        for name in NO_VALUE_FLAGS:
            if name in flags:
                level = np.where(np.asarray(flags[name], dtype=bool), np.nan, level)
        lo = int(np.searchsorted(freqs, display_start_hz, side="left"))
        hi = int(np.searchsorted(freqs, display_stop_hz, side="right"))
        searchable = np.isfinite(level) & ~exclude
        return cls(
            trace=trace, freqs=freqs, level_db=level, unit=unit, quantity=quantity,
            start_hz=display_start_hz, stop_hz=display_stop_hz, lo=lo, hi=max(lo, hi),
            searchable=searchable, flags=flags, notes=tuple(notes),
        )

    @classmethod
    def from_frame(cls, trace: int, frame: Frame, notes: tuple[str, ...] = ()) -> "TraceView":
        """1掃引分の値（Frame）から作る。表示範囲は Frame の display_start_hz〜display_stop_hz。"""
        return cls.build(
            trace, frame.axis, frame.values, frame.quantity,
            frame.display_start_hz, frame.display_stop_hz, frame.flags, notes,
        )

    @property
    def empty(self) -> bool:
        return self.hi <= self.lo

    def in_range(self, freq_hz: int) -> bool:
        return self.start_hz <= freq_hz <= self.stop_hz and not self.empty

    def nearest_index(self, freq_hz: int) -> Optional[int]:
        """表示範囲の中で freq_hz に最も近い点の番号。範囲の外なら None（マーカーは範囲の中だけ）。

        ちょうど2点の中間なら低い周波数の側。
        """
        if not self.in_range(freq_hz):
            return None
        f = self.freqs[self.lo:self.hi]
        j = int(np.searchsorted(f, freq_hz, side="left"))
        if j >= len(f):
            return self.hi - 1
        if j == 0:
            return self.lo
        # 周波数は int で差を取る（float を通さない）
        if int(f[j]) - freq_hz < freq_hz - int(f[j - 1]):
            return self.lo + j
        return self.lo + j - 1

    def freq_at(self, idx: int) -> int:
        return int(self.freqs[idx])

    def level_at(self, idx: int) -> Optional[float]:
        v = float(self.level_db[idx])
        return v if math.isfinite(v) else None

    def point_flags(self, idx: int) -> list[str]:
        """その点に付いている印の名前（読み値に添える注意: 設計G §3.3）。"""
        out = []
        for name in sorted(self.flags):
            arr = np.asarray(self.flags[name])
            if arr.shape and idx < len(arr) and bool(arr[idx]):
                out.append(name)
        return out


def highest_index(view: TraceView) -> Optional[int]:
    """ピーク: 表示範囲の中で最大の点（設計G §3.2）。印の付いた点・値なしの点は探さない。

    同じ最大値が複数あれば最も低い周波数の点。探せる点が無ければ None。
    """
    seg = np.where(view.searchable[view.lo:view.hi], view.level_db[view.lo:view.hi], -np.inf)
    if seg.size == 0 or not np.isfinite(seg).any():
        return None
    return view.lo + int(np.argmax(seg))


def find_peaks(
    view: TraceView,
    excursion_db: float = DEFAULT_EXCURSION_DB,
    threshold: Optional[float] = None,
) -> np.ndarray:
    """周りより excursion_db 以上盛り上がった点（設計G §3.2・設計P §9.6）の番号を周波数の順で返す。

    判定: その点より高い点に出会うまで（出会わなければ端まで）の左右それぞれの最小値より、
    excursion_db 以上高いこと。探せない点（印・値なし）は区間の切れ目として扱い、またいで比べない。
    区間の端の点は、外側に比べる点が無いので内側だけで判定する（内側に1点以上要る）。
    平らな頂上は最も低い周波数の点を代表にする。threshold より低いピークは除く。
    """
    if excursion_db <= 0:
        raise ValueError("excursion_db は正の値")
    level = np.where(view.searchable, view.level_db, np.nan)[view.lo:view.hi]
    found: list[int] = []
    n = len(level)
    i = 0
    while i < n:
        if not np.isfinite(level[i]):
            i += 1
            continue
        j = i
        while j < n and np.isfinite(level[j]):
            j += 1
        found.extend(view.lo + i + k for k in _peaks_in_run(level[i:j], excursion_db))
        i = j
    if threshold is not None:
        found = [k for k in found if view.level_db[k] >= threshold]
    return np.asarray(found, dtype=np.int64)


def _peaks_in_run(x: np.ndarray, excursion_db: float) -> list[int]:
    """値が続く1区間の中のピーク（find_peaks の判定）。"""
    m = len(x)
    if m < 2:
        return []
    prev_greater = _nearest_greater(x)
    next_greater = _nearest_greater(x[::-1])
    out = []
    for i in range(m):
        # 局所最大（左より真に高く、右以上）だけを候補にする。平らな頂上は左端が代表
        if i > 0 and not x[i] > x[i - 1]:
            continue
        if i < m - 1 and not x[i] >= x[i + 1]:
            continue
        # 左: より高い点（無ければ端）までの最小値からの高さ。i == 0 は外側に比べる点が無い
        if i > 0:
            a = prev_greater[i] + 1
            if x[i] - x[a:i].min() < excursion_db:
                continue
        if i < m - 1:
            r = m - 1 - i
            b = m - 1 - next_greater[r] if next_greater[r] >= 0 else m
            if x[i] - x[i + 1:b].min() < excursion_db:
                continue
        out.append(i)
    return out


def _nearest_greater(x: np.ndarray) -> np.ndarray:
    """各点について、左側で最も近い「真に高い点」の番号（無ければ −1）。"""
    res = np.full(len(x), -1, dtype=np.int64)
    stack: list[int] = []
    for i in range(len(x)):
        while stack and x[stack[-1]] <= x[i]:
            stack.pop()
        res[i] = stack[-1] if stack else -1
        stack.append(i)
    return res


LEFT = "left"
RIGHT = "right"
LOWER = "lower"  # 次に高い（いまの値より低いピークのうち最も高いもの）


def next_peak_index(
    view: TraceView,
    from_idx: int,
    direction: str,
    excursion_db: float = DEFAULT_EXCURSION_DB,
    threshold: Optional[float] = None,
) -> Optional[int]:
    """次のピーク（右・左・次に高い: 設計G §3.2）。無ければ None。"""
    peaks = find_peaks(view, excursion_db, threshold)
    if direction == RIGHT:
        cand = peaks[peaks > from_idx]
        return int(cand[0]) if cand.size else None
    if direction == LEFT:
        cand = peaks[peaks < from_idx]
        return int(cand[-1]) if cand.size else None
    if direction == LOWER:
        cur = view.level_at(from_idx)
        if cur is None:
            return None
        cand = [int(p) for p in peaks if view.level_db[p] < cur]
        if not cand:
            return None
        # 最も高いもの。同じ高さなら低い周波数
        return max(cand, key=lambda p: (view.level_db[p], -p))
    raise ValueError(f"direction が不明: {direction}")


@dataclass(frozen=True)
class ListedPeak:
    """ピークの一覧の1行（設計P §9.6: 周波数・レベル・フロアからの高さ）。"""

    freq_hz: int
    level: float
    height_db: float


def noise_floor(view: TraceView) -> Optional[float]:
    """ノイズフロア: 表示範囲の値のある点の dB の中央値（設計P §7）。"""
    seg = view.level_db[view.lo:view.hi]
    seg = seg[np.isfinite(seg)]
    if seg.size == 0:
        return None
    return float(np.median(seg))


def peak_list(
    view: TraceView,
    floor_margin_db: float,
    excursion_db: float = DEFAULT_EXCURSION_DB,
) -> tuple[Optional[float], list[ListedPeak]]:
    """ピークの一覧（設計P §9.6）: 盛り上がりの判定を満たし、フロア + floor_margin_db より高いもの。

    高い順に並べる（同じ高さは低い周波数から）。フロアの上の高さの既定値は未決（設計P §7）なので
    呼ぶ側が必ず渡す。戻り値は (フロア, 一覧)。
    """
    floor = noise_floor(view)
    if floor is None:
        return None, []
    rows = [
        ListedPeak(view.freq_at(int(p)), float(view.level_db[p]), float(view.level_db[p]) - floor)
        for p in find_peaks(view, excursion_db, floor + floor_margin_db)
    ]
    rows.sort(key=lambda r: (-r.level, r.freq_hz))
    return floor, rows


def top_peaks(
    view: TraceView,
    n: int,
    excursion_db: float = DEFAULT_EXCURSION_DB,
    threshold: Optional[float] = None,
) -> list[int]:
    """高い順に最大 n 個のピークの番号（設計G §3.5「上位 N 個」）。同じ高さは低い周波数から。"""
    peaks = [int(p) for p in find_peaks(view, excursion_db, threshold)]
    peaks.sort(key=lambda p: (-view.level_db[p], p))
    return peaks[:max(0, n)]


# ---- 境目の探索（N dB 帯域幅・しきい値との交点: 設計G §3.5） ----


@dataclass(frozen=True)
class Edge:
    """片側の境目。status が OK のときだけ freq_hz がある。"""

    status: str
    freq_hz: Optional[int] = None


@dataclass(frozen=True)
class Band:
    """境目の探索の結果。status が OK のときだけ幅・中心がある。

    status は左右のどちらかが見つからなければその側のコード（左を先に見る）。
    center_hz は下端 + 幅 // 2（端数は切り捨て。AdoptedSweep.center_hz と同じ決め方）。
    q は 中心 ÷ 帯域幅（設計G §3.5）。幅が 0 のときは None。
    """

    status: str
    left: Edge
    right: Edge
    width_hz: Optional[int] = None
    center_hz: Optional[int] = None
    q: Optional[float] = None


def _interp_crossing(f_in: int, l_in: float, f_out: int, l_out: float, target: float) -> int:
    """内側の点と外側の点の間で、値が target になる周波数を dB の線形補間で求め Hz の int に丸める。

    周波数は「内側の点 + 差の分」で計算し、差の分だけを float から偶数丸めで int にする
    （大きい周波数を float にしないため）。
    """
    t = (l_in - target) / (l_in - l_out)
    return f_in + int(round((f_out - f_in) * t))


def _find_edge(view: TraceView, idx: int, step: int, inside: Callable[[float], bool], target: float) -> Edge:
    """idx から step（−1: 左、+1: 右）の向きに、inside でなくなる最初の点を探す。

    値なしの点に達したら NO_VALUE_REGION（段差を交点として拾わない: 設計E §6.4）。
    表示範囲の端まで inside のままなら EDGE。
    """
    k = idx
    while True:
        nxt = k + step
        if nxt < view.lo or nxt >= view.hi:
            return Edge(EDGE)
        v = view.level_db[nxt]
        if not math.isfinite(v):
            return Edge(NO_VALUE_REGION)
        if not inside(float(v)):
            return Edge(OK, _interp_crossing(view.freq_at(k), float(view.level_db[k]), view.freq_at(nxt), float(v), target))
        k = nxt


def _band(view: TraceView, idx: int, inside: Callable[[float], bool], target: float) -> Band:
    left = _find_edge(view, idx, -1, inside, target)
    right = _find_edge(view, idx, +1, inside, target)
    if left.status != OK:
        return Band(left.status, left, right)
    if right.status != OK:
        return Band(right.status, left, right)
    width = right.freq_hz - left.freq_hz
    center = left.freq_hz + width // 2
    q = center / width if width > 0 else None
    return Band(OK, left, right, width, center, q)


def n_db_bandwidth(view: TraceView, idx: int, n_db: float) -> Band:
    """N dB 帯域幅（設計G §3.5）: idx の値から n_db 下がる点を左右に探す。

    境目は「起点の値 − n_db 以下になった最初の点」とその手前の点の間を補間した周波数。
    """
    if n_db <= 0:
        raise ValueError("n_db は正の値")
    base = view.level_at(idx)
    if base is None:
        return Band(NO_MARKER_VALUE, Edge(NO_MARKER_VALUE), Edge(NO_MARKER_VALUE))
    target = base - n_db
    return _band(view, idx, lambda v: v > target, target)


def threshold_band(view: TraceView, idx: int, level: float, side: str) -> Band:
    """しきい値との交点（設計G §3.5）: idx を含み、値がしきい値の side 側にある範囲の両端と幅。

    SIDE_BELOW は level 以下、SIDE_ABOVE は level 以上を内側とする（しきい値ちょうどは内側）。
    """
    if side == SIDE_BELOW:
        inside: Callable[[float], bool] = lambda v: v <= level
    elif side == SIDE_ABOVE:
        inside = lambda v: v >= level
    else:
        raise ValueError(f"side が不明: {side}")
    base = view.level_at(idx)
    if base is None:
        return Band(NO_MARKER_VALUE, Edge(NO_MARKER_VALUE), Edge(NO_MARKER_VALUE))
    if not inside(base):
        return Band(NOT_IN_REGION, Edge(NOT_IN_REGION), Edge(NOT_IN_REGION))
    return _band(view, idx, inside, level)
