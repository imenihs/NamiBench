"""マーカーが読むトレースの見方と、ピーク・帯域幅・しきい値の探索（設計G §3.2・§3.5）。

- 入力は1本のトレースの周波数軸とリニアの値（と量の種類）、表示範囲 Start〜Stop だけ
- マーカーは表示範囲の中の点にだけ載る（補間しない: 設計G §3.1）。
  補間するのは N dB 帯域幅・しきい値の交点の周波数だけで、Hz の int に丸めて返す
- 値なしの点（ノーマライズの基準が無い点・dB にできない値）は読まない・探さない（設計E §6.4）
- 純粋な計算だけ。Qt・スレッド・入出力を持たない
"""
from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from ..trace import formats
from ..types import FreqAxis, Frame, Quantity

# 探す対象から外す点の印（設計G §3.2「前置き不足・範囲外」、設計E §6.4「基準の無い点」）
EXCLUDE_FLAGS = ("transient", "out_of_cal_range", "no_reference")
# 値そのものが無い点の印（設計E §6.4: マーカーは「値なし」と出す）
NO_VALUE_FLAGS = ("no_reference",)
# 値はあるが、ピークとして選ばない点の印（設計J §4.8: 0Hz の局発の漏れの山は入力の信号ではなく、
# そこに載るピークは意味の無い結果）。盛り上がりの比べ方では区間を切らずに比べる点として残す
# ——切ると帯の端の裾が「外側に比べる点の無い端の点」になり、ピークとして拾われるため。
# 手で置く M（周波数の指定・クリック）はこの印の点にも置ける
PEAK_SKIP_FLAGS = ("lo_leak",)

UNIT_DBM = "dBm"
UNIT_DB = "dB"

# 次のピークの既定の盛り上がり（設計G §3.2: 周りより 3dB 以上。値は設定で変えられる）
DEFAULT_EXCURSION_DB = 3.0

# 探すもの（設計G §3.8）: 山（周りより盛り上がった点・＋ピーク）と谷（周りよりへこんだ点・−ピーク）。
# 谷は山と同じ判定を値の上下を返して当てる（BEF のノッチ・S11 の共振を探す）
PEAK = "peak"
DIP = "dip"
POLARITIES = (PEAK, DIP)

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
    # 表示の形式（設計R §3: db・gamma・swr）。level_db は dB のまま持ち（ピーク・盛り上がり・N dB 帯域幅は dB で探す）、
    # 読み値（level_at）としきい値だけを形式の値で扱う。unit は形式の単位（|Γ|・SWR）
    fmt: str = formats.DB

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
    def from_levels(cls, trace: int, xs: np.ndarray, level_db: np.ndarray, quantity: Quantity, start: int, stop: int,
                    flags: Optional[dict] = None, notes: tuple[str, ...] = ()) -> "TraceView":
        """横軸の位置（int64・昇順）と dB の値から作る。ゼロスパンの時間軸（設計N §1: 位置はマイクロ秒）に使う。

        値なしの点は NaN で渡す。印・表示範囲・探してよい点の決め方は build と同じ。
        """
        xs = np.asarray(xs, dtype=np.int64)
        level = np.asarray(level_db, dtype=np.float64)
        level = np.where(np.isfinite(level), level, np.nan)
        flags = {k: np.asarray(v, dtype=bool) for k, v in (flags or {}).items() if np.shape(v) == xs.shape}
        exclude = np.zeros(xs.size, dtype=bool)
        for name in EXCLUDE_FLAGS:
            if name in flags:
                exclude |= flags[name]
        lo = int(np.searchsorted(xs, int(start), side="left"))
        hi = int(np.searchsorted(xs, int(stop), side="right"))
        return cls(
            trace=trace, freqs=xs, level_db=level, unit=UNIT_DBM if quantity == Quantity.POWER else UNIT_DB,
            quantity=quantity, start_hz=int(start), stop_hz=int(stop), lo=lo, hi=max(lo, hi),
            searchable=np.isfinite(level) & ~exclude, flags=flags, notes=tuple(notes),
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
        """その点の読み値（表示の形式の値。dB の形式なら dB）。値なしは None。"""
        v = float(self.level_db[idx])
        if not math.isfinite(v):
            return None
        return v if self.fmt == formats.DB else formats.value_from_db(v, self.fmt)

    def display(self, level_db: Optional[float]) -> Optional[float]:
        """dB の値を、このトレースの表示の形式の値にする（レベルのカーソル・帯域幅の線）。"""
        if level_db is None or not math.isfinite(level_db):
            return None
        return level_db if self.fmt == formats.DB else formats.value_from_db(level_db, self.fmt)

    def db_of(self, value: float) -> float:
        """表示の形式の値（しきい値など）を dB にする。"""
        return float(value) if self.fmt == formats.DB else formats.to_db(value, self.fmt)

    @property
    def peak_candidate(self) -> np.ndarray:
        """ピークとして選んでよい点（searchable から PEAK_SKIP_FLAGS の点を除いたもの）。"""
        out = self.searchable.copy()
        for name in PEAK_SKIP_FLAGS:
            a = self.flags.get(name)
            if a is not None and np.shape(a) == out.shape:
                out &= ~np.asarray(a, dtype=bool)
        return out

    def point_flags(self, idx: int) -> list[str]:
        """その点に付いている印の名前（読み値に添える注意: 設計G §3.3）。"""
        out = []
        for name in sorted(self.flags):
            arr = np.asarray(self.flags[name])
            if arr.shape and idx < len(arr) and bool(arr[idx]):
                out.append(name)
        if self.fmt == formats.SWR:
            lim = formats.swr_limit_mask(self.level_db[idx:idx + 1], self.fmt)
            if lim is not None and bool(lim[0]):
                out.append(formats.FLAG_SWR_LIMIT)  # SWR の上限に当てた点（設計R §3。値は SWR_MAX）
        return out


def highest_index(view: TraceView) -> Optional[int]:
    """ピーク: 表示範囲の中で最大の点（設計G §3.2）。印の付いた点・値なしの点・0Hz の局発の漏れの帯の点
    （PEAK_SKIP_FLAGS: 設計J §4.8）は探さない。

    同じ最大値が複数あれば最も低い周波数の点。探せる点が無ければ None。
    """
    seg = np.where(view.peak_candidate[view.lo:view.hi], view.level_db[view.lo:view.hi], -np.inf)
    if seg.size == 0 or not np.isfinite(seg).any():
        return None
    return view.lo + int(np.argmax(seg))


def check_polarity(polarity: str) -> str:
    if polarity not in POLARITIES:
        raise ValueError(f"polarity が不明: {polarity}")
    return polarity


def oriented(view: TraceView, polarity: str) -> TraceView:
    """谷を探すときは値の上下を返したトレース（谷が山になる）。周波数・印・表示範囲はそのまま（設計G §3.8）。

    返したトレースで山を探すと、谷の判定（周りより excursion_db 以上へこんだ点）・一番深い点・
    次に深い谷が、山と同じ関数で求まる。値なしの点（NaN）は返しても値なしのまま。
    探す側は dB で比べるので、返すトレースの表示の形式は dB にする（level_at が dB を返す。設計R §3 の |Γ|・SWR でも
    探す点は同じ）。
    """
    if check_polarity(polarity) == PEAK:
        return view if view.fmt == formats.DB else dataclasses.replace(view, fmt=formats.DB)
    return dataclasses.replace(view, level_db=-view.level_db, fmt=formats.DB)


def db_at(view: TraceView, idx: int) -> Optional[float]:
    """その点の dB の値（表示の形式によらない）。値なしは None。"""
    v = float(view.level_db[idx])
    return v if math.isfinite(v) else None


def _oriented_threshold(threshold: Optional[float], polarity: str) -> Optional[float]:
    """しきい値も向きを合わせる（谷では「これより高い谷は無視」になる）。"""
    if threshold is None or polarity == PEAK:
        return threshold
    return -threshold


def extreme_index(view: TraceView, polarity: str = PEAK) -> Optional[int]:
    """「ピーク」（山: 最大の点）・「谷」（一番深い点＝−ピーク）。探す点の決まりは highest_index と同じ。"""
    return highest_index(oriented(view, polarity))


def find_extrema(view: TraceView, excursion_db: float = DEFAULT_EXCURSION_DB, threshold: Optional[float] = None,
                 polarity: str = PEAK) -> np.ndarray:
    """山（find_peaks）か谷（周りより excursion_db 以上へこんだ点）の番号を周波数の順で返す。

    谷の threshold は「これより高い谷は除く」（山の「これより低いピークは除く」の向きを逆にしたもの）。
    """
    return find_peaks(oriented(view, polarity), excursion_db, _oriented_threshold(threshold, polarity))


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
    0Hz の局発の漏れの帯の点（PEAK_SKIP_FLAGS: 設計J §4.8）は比べる相手には使うが、ピークとしては返さない。
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
    candidate = view.peak_candidate
    found = [k for k in found if candidate[k]]
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
    polarity: str = PEAK,
) -> Optional[int]:
    """次のピーク（右・左・次に高い: 設計G §3.2）。無ければ None。

    谷（設計G §3.8）では次の谷（右・左）と「次に深い」（いまより浅い谷のうち最も深いもの）。
    """
    view = oriented(view, polarity)
    threshold = _oriented_threshold(threshold, polarity)
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


# ピークの一覧の基準（設計P §9.7）: 電力はノイズフロア、基準との比は表示範囲の最大
BASE_NOISE_FLOOR = "noise_floor"
BASE_MAX = "max"


@dataclass(frozen=True)
class ListedPeak:
    """ピークの一覧の1行（設計P §9.7: 周波数・レベル・基準からの差）。

    diff_db は level − 基準。電力ではノイズフロアからの高さ（正）、比では最大からの差（0 以下）。
    """

    freq_hz: int
    level: float
    diff_db: float


@dataclass(frozen=True)
class PeakList:
    """ピークの一覧（設計P §9.7）。base は BASE_NOISE_FLOOR か BASE_MAX、base_level はその値（無ければ None）。"""

    base: str
    base_level: Optional[float]
    polarity: str
    rows: list[ListedPeak]


class DipsOnPower(ValueError):
    """電力のスペクトルでは谷の一覧を作らない（ノイズフロアの下を探す意味が無い: 設計P §9.7）。"""


def noise_floor(view: TraceView) -> Optional[float]:
    """ノイズフロア: 表示範囲の値のある点の dB の中央値（設計P §7）。"""
    seg = view.level_db[view.lo:view.hi]
    seg = seg[np.isfinite(seg)]
    if seg.size == 0:
        return None
    return float(np.median(seg))


def list_base(view: TraceView) -> tuple[str, Optional[float]]:
    """ピークの一覧の基準（設計P §9.7）。

    電力（SA）はノイズフロア（中央値）。基準との比（TG・S11）はノイズフロアが無く、中央値は画面のうち通過域と
    阻止域の占める割合で動くので、表示範囲の最大（「ピーク」と同じ点: 印の付いた点・局発の漏れの帯を除く）。
    """
    if view.quantity == Quantity.POWER:
        return BASE_NOISE_FLOOR, noise_floor(view)
    idx = highest_index(view)
    return BASE_MAX, None if idx is None else db_at(view, idx)


def peak_list(
    view: TraceView,
    margin_db: float,
    excursion_db: float = DEFAULT_EXCURSION_DB,
    polarity: str = PEAK,
) -> PeakList:
    """ピークの一覧（設計P §9.6・§9.7）。盛り上がり（谷はへこみ）の判定を満たすもののうち:

    - 電力: 山 = ノイズフロア + margin_db 以上。谷は探さない（DipsOnPower）
    - 比: 山 = 最大 − margin_db 以上（通過域の山）、谷 = 最大 − margin_db 以下（ノッチ・共振）

    山は高い順、谷は深い順（同じ値は低い周波数から）。margin_db の既定は呼ぶ側が渡す（設計P §9.1: 10dB）。
    """
    check_polarity(polarity)
    base, level = list_base(view)
    if base == BASE_NOISE_FLOOR and polarity == DIP:
        raise DipsOnPower()
    if level is None:
        return PeakList(base, None, polarity, [])
    limit = level + margin_db if base == BASE_NOISE_FLOOR else level - margin_db
    rows = [
        ListedPeak(view.freq_at(int(p)), float(view.level_db[p]), float(view.level_db[p]) - level)
        for p in find_extrema(view, excursion_db, limit, polarity)
    ]
    sign = -1.0 if polarity == PEAK else 1.0
    rows.sort(key=lambda r: (sign * r.level, r.freq_hz))
    return PeakList(base, level, polarity, rows)


def top_peaks(
    view: TraceView,
    n: int,
    excursion_db: float = DEFAULT_EXCURSION_DB,
    threshold: Optional[float] = None,
    polarity: str = PEAK,
) -> list[int]:
    """高い順に最大 n 個のピークの番号（設計G §3.5「上位 N 個」）。同じ高さは低い周波数から。

    谷（設計P §9.7）は深い順に最大 n 個。
    """
    v = oriented(view, polarity)
    peaks = [int(p) for p in find_peaks(v, excursion_db, _oriented_threshold(threshold, polarity))]
    peaks.sort(key=lambda p: (-v.level_db[p], p))
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


def n_db_bandwidth(view: TraceView, idx: int, n_db: float, polarity: str = PEAK) -> Band:
    """N dB 帯域幅（設計G §3.5）: idx の値から n_db 下がる点を左右に探す。

    境目は「起点の値 − n_db 以下になった最初の点」とその手前の点の間を補間した周波数。
    谷に置いたマーカー（設計G §3.8）では、谷から n_db 上がる点を左右に探す（ノッチの幅）。
    値の上下を返したトレースで同じ探し方をする（交点の周波数は返しても同じ）。
    """
    if n_db <= 0:
        raise ValueError("n_db は正の値")
    view = oriented(view, polarity)
    base = view.level_at(idx)
    if base is None:
        return Band(NO_MARKER_VALUE, Edge(NO_MARKER_VALUE), Edge(NO_MARKER_VALUE))
    target = base - n_db
    return _band(view, idx, lambda v: v > target, target)


def threshold_band(view: TraceView, idx: int, level: float, side: str) -> Band:
    """しきい値との交点（設計G §3.5）: idx を含み、値がしきい値の side 側にある範囲の両端と幅。

    SIDE_BELOW は level 以下、SIDE_ABOVE は level 以上を内側とする（しきい値ちょうどは内側）。
    level は表示の形式の値（例 SWR 2）。形式は |Γ| について単調に増えるので、dB に直して dB の値で探す（設計R §3）。
    """
    level = view.db_of(level)
    view = oriented(view, PEAK)  # 探すのは dB の値（level_at が dB を返す）
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
