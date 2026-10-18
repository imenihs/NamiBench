"""マーカー表（設計G §3.1〜§3.5）。

- P（ピーク）と M（手動）を1つの表に持つ。番号は種類ごとに 1〜MAX_PER_KIND で、空いている最小の番号を使う
- マーカーはトレースの点に載る（補間しない）。持つのは「置いた周波数」（target_hz）で、読むときに
  いまの軸の最も近い点へ載せる。軸が変わるたびに置き直すと周波数が少しずつずれていくため、
  置いた周波数そのものは書き換えない（書き換えるのは利用者の操作とピーク追従だけ）
- 表示範囲 Start〜Stop の外に出たマーカー・カーソルは消さず「範囲外」とする（U-15）
- 条件を変える命令（設計G §3.4）は SweepRequest か REF レベルの値を返すだけで、機器を呼ばない
- 結果はコード（"out_of_range" など）で返す。画面の文言は画面側が決める（U-22）
- 純粋な計算だけ。Qt・スレッド・入出力を持たない
"""
from __future__ import annotations

import enum
import math
from dataclasses import dataclass, field
from typing import Mapping, Optional

from ..types import Quantity, SweepRequest
from . import analysis as an
from .analysis import TraceView

# P・M それぞれの最大数（設計G §3.5 の「最大 8（仮）」）
MAX_PER_KIND = 8

# 読み値・操作の結果のコード
ST_OK = "ok"
ST_OUT_OF_RANGE = "out_of_range"  # 表示範囲の外（設計G §3.5: 消さずに「範囲外」）
ST_NO_VALUE = "no_value"  # 載った点に値が無い（設計E §6.4: 基準の範囲外など）
ST_NO_TRACE = "no_trace"  # 載るトレースのデータが渡されていない
ST_PARENT_UNAVAILABLE = "parent_unavailable"  # 親のマーカーが読めない（オフセットのカーソル）
ST_NO_REF = "no_ref"  # デルタの基準が無い
ST_REF_UNAVAILABLE = "ref_unavailable"  # デルタの基準のマーカーが読めない
ST_UNIT_MISMATCH = "unit_mismatch"  # 電力（dBm）と比（dB）の差は作らない（設計F §5.4 と同じ考え方）
ST_TABLE_FULL = "table_full"
ST_NO_PEAK = "no_peak"
ST_SAME_FREQUENCY = "same_frequency"  # デルタの2点が同じ周波数で Start・Stop にできない


class Kind(str, enum.Enum):
    """マーカーの種類（設計G §3.5）。"""

    PEAK = "P"
    MANUAL = "M"


class Sign(str, enum.Enum):
    """オフセットの向き（設計G §3.5: +Δ・−Δ・±Δ）。"""

    PLUS = "+"
    MINUS = "-"
    BOTH = "±"

    def signs(self) -> tuple[int, ...]:
        return {Sign.PLUS: (1,), Sign.MINUS: (-1,), Sign.BOTH: (1, -1)}[self]


@dataclass(frozen=True)
class FreqOffset:
    """周波数のオフセットのカーソル（親の周波数 ± delta_hz に縦線）。"""

    sign: Sign
    delta_hz: int


@dataclass(frozen=True)
class LevelOffset:
    """レベルのオフセットのカーソル（親のレベル ± delta_db に横線）。"""

    sign: Sign
    delta_db: float


@dataclass(frozen=True)
class ThresholdSetting:
    """しきい値との交点の設定。side は analysis.SIDE_BELOW / SIDE_ABOVE。

    fmt はしきい値を決めたときのトレースの表示の形式（設計R §3: db・gamma・swr）。形式を変えたら、同じ反射の大きさを
    いまの形式の値に直して探す・出す（SWR 2 で決めたしきい値は |Γ| では 0.33。数字のまま別の意味にしない）。
    power は電力のトレース（dBm・dBμV など）で決めたしきい値か。電力の単位・外部補正を替えたときに同じだけずらすのは
    これだけ（設計書 O §1.4。比の dB・|Γ|・SWR のしきい値は電力の単位に関係しない）。
    ref は TG の基準との比のトレースで決めたしきい値なら、その基準の id（設計E §6.7）。基準の値を直したときに同じだけ
    ずらすのはこれだけ（S11 のリターンロス・NF の dB・電力・|Γ|・SWR で決めたしきい値は基準の値に関係しない）。TG の比は
    反射の形式（|Γ|・SWR）へ直さない。
    """

    level: float
    side: str
    fmt: str = an.formats.DB
    power: bool = False
    ref: Optional[str] = None

    def level_in(self, fmt: str, ratio_view: bool = True) -> float:
        """fmt の形式のトレースで使うしきい値。直すのは、比で決めたしきい値を比のトレースで読むときだけ（比の形式の間:
        設計R §3）。電力で決めたしきい値・TG の基準との比で決めたしきい値・電力のトレースでは数字のまま。"""
        if self.power or self.ref is not None or not ratio_view:
            return self.level
        return an.formats.convert_value(self.level, self.fmt, fmt)


@dataclass
class Marker:
    """表の1行（設計G §3.1・§3.5）。

    target_hz は置いた周波数。表示する周波数は、いまの軸で target_hz に最も近い点の周波数。
    ref はデルタの基準のマーカーの番号（"P1" など）。peak_track は P だけが持てる。
    bandwidth_n_db は N dB 帯域幅の N（0〜2個。2個ならシェープファクタも出す）。
    polarity は置いたときの向き（山 analysis.PEAK・谷 analysis.DIP: 設計G §3.8）。ピーク追従はこの向きで
    追いかけ、N dB 帯域幅は谷なら N dB 上がる点を探す。置いた後で画面の山／谷の選択を変えても変わらない。
    手で動かす（←→・周波数を打つ・セルを直す）間は変えず、ピーク・谷を探す操作で動かしたときだけ、その向きにする。
    """

    kind: Kind
    number: int
    trace: int
    target_hz: int
    ref: Optional[str] = None
    peak_track: bool = False
    freq_offsets: list[FreqOffset] = field(default_factory=list)
    level_offsets: list[LevelOffset] = field(default_factory=list)
    bandwidth_n_db: tuple[float, ...] = ()
    threshold: Optional[ThresholdSetting] = None
    polarity: str = an.PEAK

    @property
    def id(self) -> str:
        return f"{self.kind.value}{self.number}"


@dataclass
class XYCursors:
    """自由な XY カーソル（設計G §3.5）。トレースに載らず、値を読まない。

    x は周波数（Hz の int）、y はレベル（縦軸の単位の数値）。None は置いていない。
    y1_kind・y2_kind は y を置いたときの縦軸の単位の種類（Y_KINDS: "power"＝電力の単位・"db"・"gamma"・"swr"＝S11 の
    比の表示の形式・"tg"＝TG の基準との比の dB・"nf"＝NF の dB。None は分からない前の版）。y は置いたときの単位にだけ従って
    直す: 電力の値は電力の縦軸のときに電力の単位・外部補正の分だけずらし、S11 の比の形式の値は比の縦軸の形式が替わったときに
    同じ反射の大きさへ直し（設計書 O §1.4・設計R §3）、TG の基準との比の値はその基準の値を直したときだけ同じだけずらす
    （y1_ref・y2_ref はその基準の id: 設計E §6.7）。
    """

    x1_hz: Optional[int] = None
    x2_hz: Optional[int] = None
    y1: Optional[float] = None
    y2: Optional[float] = None
    y1_kind: Optional[str] = None
    y2_kind: Optional[str] = None
    y1_ref: Optional[str] = None
    y2_ref: Optional[str] = None


# XY カーソルの y を置いたときの縦軸の単位の種類（電力・S11 の比の表示の形式・TG の基準との比・NF の dB）。NF の縦軸
# （設計M）で置いた y は電力の単位でずらさず、比の形式としても直さない。TG の基準との比（設計E §6.7）で置いた y は
# その基準の値を直したときだけずらし、反射の形式（|Γ|・SWR）へは直さない
Y_KIND_POWER = "power"
Y_KIND_NF = "nf"
Y_KIND_TG = "tg"
Y_KINDS = (Y_KIND_POWER, an.formats.DB, an.formats.GAMMA, an.formats.SWR, Y_KIND_NF, Y_KIND_TG)


@dataclass(frozen=True)
class OpResult:
    """操作の結果。requested_hz と freq_hz が違えば、点に載せたことを1回知らせる（M-02）。"""

    code: str
    marker_id: Optional[str] = None
    requested_hz: Optional[int] = None
    freq_hz: Optional[int] = None

    @property
    def ok(self) -> bool:
        return self.code == ST_OK

    @property
    def snapped(self) -> bool:
        return self.ok and self.requested_hz is not None and self.requested_hz != self.freq_hz


@dataclass(frozen=True)
class Command:
    """マーカーから条件を変える命令（設計G §3.4）。機器は呼ばない。

    request は掃引条件の変更（Center・Start/Stop）。ref_level と unit は REF レベルの変更。
    """

    code: str
    request: Optional[SweepRequest] = None
    ref_level: Optional[float] = None
    unit: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.code == ST_OK


Views = Mapping[int, TraceView]


def _check_hz(name: str, value: object) -> None:
    # 周波数は int で受ける（設計A §2.1）
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} は int（Hz）である必要があります（{type(value).__name__} を受け取りました）")


def pick_trace(views: Views, freq_hz: int, level: float) -> Optional[int]:
    """クリック位置（周波数・縦軸の値）に縦方向で最も近いトレースの番号（設計G §3.2）。

    views には見えているトレースだけを渡す。同じ近さなら番号の小さいトレース。
    """
    _check_hz("freq_hz", freq_hz)
    best: Optional[tuple[float, int]] = None
    for no in sorted(views):
        v = views[no]
        idx = v.nearest_index(freq_hz)
        if idx is None:
            continue
        lv = v.level_at(idx)
        if lv is None:
            continue
        d = abs(lv - level)
        if best is None or d < best[0]:
            best = (d, no)
    return None if best is None else best[1]


def reading_at(view: TraceView, freq_hz: int) -> dict:
    """マウスの位置の読み値（設計G §2: 波形の値）。最も近い点の周波数と値。"""
    _check_hz("freq_hz", freq_hz)
    idx = view.nearest_index(freq_hz)
    if idx is None:
        return {"status": ST_OUT_OF_RANGE, "freq_hz": None, "level": None, "unit": view.unit}
    lv = view.level_at(idx)
    return {
        "status": ST_OK if lv is not None else ST_NO_VALUE,
        "freq_hz": view.freq_at(idx),
        "level": lv,
        "unit": view.unit,
    }


class MarkerTable:
    """P と M のマーカー表・XY カーソル・ピーク探索の設定（設計G §3.5）。

    excursion_db は次のピークの盛り上がりの判定（既定 3dB: 設計G §3.2）。
    peak_threshold はこれより低いピークを無視する（任意）。
    """

    def __init__(self) -> None:
        self._markers: dict[str, Marker] = {}
        self.xy = XYCursors()
        self.excursion_db: float = an.DEFAULT_EXCURSION_DB
        self.peak_threshold: Optional[float] = None

    # ---- 表の出し入れ ----

    def markers(self) -> list[Marker]:
        """表の並び: P を番号順、続けて M を番号順。"""
        order = {Kind.PEAK: 0, Kind.MANUAL: 1}
        return sorted(self._markers.values(), key=lambda m: (order[m.kind], m.number))

    def get(self, marker_id: str) -> Marker:
        return self._markers[marker_id]

    def _free_number(self, kind: Kind) -> Optional[int]:
        used = {m.number for m in self._markers.values() if m.kind == kind}
        for n in range(1, MAX_PER_KIND + 1):
            if n not in used:
                return n
        return None

    def add(self, kind: Kind, trace: int, freq_hz: int, view: Optional[TraceView] = None,
            polarity: str = an.PEAK) -> OpResult:
        """空いている最小の番号でマーカーを作る。view を渡すと最も近い点に載せる（設計G §3.1）。

        範囲の外の周波数には置かない（マーカーは表示範囲の中だけ）。polarity は置いたときの向き（設計G §3.8）。
        """
        _check_hz("freq_hz", freq_hz)
        an.check_polarity(polarity)
        n = self._free_number(kind)
        if n is None:
            return OpResult(ST_TABLE_FULL, requested_hz=freq_hz)
        adopted = freq_hz
        if view is not None:
            idx = view.nearest_index(freq_hz)
            if idx is None:
                return OpResult(ST_OUT_OF_RANGE, requested_hz=freq_hz)
            adopted = view.freq_at(idx)
        m = Marker(kind=kind, number=n, trace=trace, target_hz=adopted, polarity=polarity)
        self._markers[m.id] = m
        return OpResult(ST_OK, m.id, freq_hz, adopted)

    def remove(self, marker_id: str) -> None:
        """消す。これを基準にしていた行のデルタの基準も外す。"""
        del self._markers[marker_id]
        for m in self._markers.values():
            if m.ref == marker_id:
                m.ref = None

    def clear(self) -> None:
        self._markers.clear()

    def set_trace(self, marker_id: str, trace: int) -> None:
        """載るトレースを替える。置いた周波数はそのまま（読むときに新しいトレースの最も近い点へ載る）。"""
        if isinstance(trace, bool) or not isinstance(trace, int):
            raise TypeError("trace は int である必要があります")
        self._markers[marker_id].trace = trace

    # ---- 置き方の書き出し・読み戻し（状態の保存・保存ファイル: 設計O §1.2・設計H §7.1） ----

    def to_defs(self) -> dict:
        """表の置き方（読み値を含まない）を JSON にできる形で返す。

        markers は表の並び（P・M を番号順）で、番号・種類・トレース・置いた周波数・デルタの基準・
        ピーク追従・オフセット・機能を持つ。xy は XY カーソル、excursion_db・peak_threshold はピーク探索の設定。
        """
        return {
            "markers": [marker_to_def(m) for m in self.markers()],
            "xy": {"x1_hz": self.xy.x1_hz, "x2_hz": self.xy.x2_hz, "y1": self.xy.y1, "y2": self.xy.y2,
                   "y1_kind": self.xy.y1_kind, "y2_kind": self.xy.y2_kind,
                   "y1_ref": self.xy.y1_ref, "y2_ref": self.xy.y2_ref},
            "excursion_db": float(self.excursion_db),
            "peak_threshold": None if self.peak_threshold is None else float(self.peak_threshold),
        }

    def from_defs(self, defs) -> list[int]:
        """to_defs の形から表を作り直す。番号とデルタの相手をそのまま戻す。

        defs は to_defs の辞書か、markers の行の並びだけ（古い保存の形）。読めない行・番号が範囲の外の行・
        同じ番号の2行目は飛ばし、飛ばした行の位置（0 始まり）を返す。相手のいないデルタの基準は外す。
        xy が読めないときは XY カーソルを外したままにする。
        """
        if isinstance(defs, dict):
            rows = defs.get("markers") or []
            xy = defs.get("xy")
            exc = defs.get("excursion_db")
            thr = defs.get("peak_threshold", self.peak_threshold)
        else:
            rows, xy, exc, thr = defs or [], None, None, self.peak_threshold
        self._markers.clear()
        self.xy = XYCursors()
        skipped: list[int] = []
        for i, d in enumerate(rows):
            try:
                m = marker_from_def(d)
            except (KeyError, TypeError, ValueError, AttributeError):
                skipped.append(i)
                continue
            if not 1 <= m.number <= MAX_PER_KIND or m.id in self._markers:
                skipped.append(i)
                continue
            if m.kind != Kind.PEAK:
                m.peak_track = False  # ピーク追従は P だけ（set_peak_track と同じ決まり）
            self._markers[m.id] = m
        for m in self._markers.values():
            if m.ref is not None and (m.ref not in self._markers or m.ref == m.id):
                m.ref = None
        if isinstance(xy, dict):
            try:
                self.set_xy(**{k: xy.get(k) for k in ("x1_hz", "x2_hz", "y1", "y2")})
                for k in ("y1", "y2"):
                    kind = xy.get(k + "_kind")
                    ref = xy.get(k + "_ref")
                    self.set_y_kind(k, kind if kind in Y_KINDS else None, ref if isinstance(ref, str) else None)
            except (KeyError, TypeError, ValueError):
                self.xy = XYCursors()
        try:
            if exc is not None and float(exc) > 0:
                self.excursion_db = float(exc)
            self.peak_threshold = None if thr is None else float(thr)
        except (TypeError, ValueError):
            pass
        return skipped

    # ---- 置き方・動かし方（設計G §3.2） ----

    def set_frequency(self, marker_id: str, freq_hz: int, view: TraceView) -> OpResult:
        """数値で周波数を入れる。最も近い点に載せ、実際の周波数を返す（違えば1回知らせる: M-02）。"""
        _check_hz("freq_hz", freq_hz)
        m = self._markers[marker_id]
        idx = view.nearest_index(freq_hz)
        if idx is None:
            return OpResult(ST_OUT_OF_RANGE, marker_id, freq_hz)
        m.target_hz = view.freq_at(idx)
        return OpResult(ST_OK, marker_id, freq_hz, m.target_hz)

    def step(self, marker_id: str, points: int, view: TraceView) -> OpResult:
        """←→キー: points 点動かす（Shift は呼ぶ側が 10 を渡す）。表示範囲の端で止まる。"""
        m = self._markers[marker_id]
        idx = view.nearest_index(m.target_hz)
        if idx is None:
            return OpResult(ST_OUT_OF_RANGE, marker_id, m.target_hz)
        idx = min(max(idx + points, view.lo), view.hi - 1)
        m.target_hz = view.freq_at(idx)
        return OpResult(ST_OK, marker_id, None, m.target_hz)

    def place_at_click(self, views: Views, freq_hz: int, level: float, polarity: str = an.PEAK) -> OpResult:
        """クリック: 縦方向で最も近いトレースの最も近い点に、空いている番号の M を置く（設計G §3.2・§3.5）。"""
        trace = pick_trace(views, freq_hz, level)
        if trace is None:
            return OpResult(ST_OUT_OF_RANGE, requested_hz=freq_hz)
        return self.add(Kind.MANUAL, trace, freq_hz, views[trace], polarity)

    def peak_search(self, marker_id: Optional[str], view: TraceView, polarity: str = an.PEAK) -> OpResult:
        """「ピーク」: 選んでいる行を表示範囲の最大の点へ動かす。選んでいなければ M を作る（設計G §3.5）。

        谷（設計G §3.8）では一番深い点へ。動かした行はその向きになる。選んでいる行があれば view はその行のトレースのもの。
        """
        idx = an.extreme_index(view, polarity)
        if idx is None:
            return OpResult(ST_NO_PEAK, marker_id)
        f = view.freq_at(idx)
        if marker_id is None:
            return self.add(Kind.MANUAL, view.trace, f, view, polarity)
        m = self._markers[marker_id]
        m.target_hz = f
        m.polarity = polarity
        return OpResult(ST_OK, marker_id, None, f)

    def next_peak(self, marker_id: str, direction: str, view: TraceView, polarity: str = an.PEAK) -> OpResult:
        """「次のピーク ◀ ▶」「次に高い」（谷では「次の谷」「次に深い」: 設計G §3.8）: 選んでいる行を動かす
        （設計G §3.2）。無ければ動かさない（向きも変えない）。"""
        m = self._markers[marker_id]
        idx = view.nearest_index(m.target_hz)
        if idx is None:
            return OpResult(ST_OUT_OF_RANGE, marker_id, m.target_hz)
        nxt = an.next_peak_index(view, idx, direction, self.excursion_db, self.peak_threshold, polarity)
        if nxt is None:
            return OpResult(ST_NO_PEAK, marker_id)
        m.target_hz = view.freq_at(nxt)
        m.polarity = polarity
        return OpResult(ST_OK, marker_id, None, m.target_hz)

    def register_peak(self, view: TraceView, freq_hz: int, polarity: str = an.PEAK) -> OpResult:
        """ピークの一覧の行の「P に登録」: 空いている番号の P にする（設計G §3.5）。谷の一覧からは谷の P（設計P §9.7）。"""
        return self.add(Kind.PEAK, view.trace, freq_hz, view, polarity)

    def place_top_peaks(self, view: TraceView, n: int, polarity: str = an.PEAK) -> list[OpResult]:
        """「上位 N 個に P を置く」: 高い順に P1〜PN へ置く（設計G §3.5）。谷は深い順（設計P §9.7）。

        すでにある Pk は周波数とトレースと向きだけを置き換え、他の設定（デルタの基準・オフセット・追従）は残す。
        N は MAX_PER_KIND までに収める。見つかったピークが N より少なければ見つかった数だけ置く。
        """
        n = min(max(n, 0), MAX_PER_KIND)
        results = []
        for k, idx in enumerate(an.top_peaks(view, n, self.excursion_db, self.peak_threshold, polarity), start=1):
            f = view.freq_at(idx)
            mid = f"{Kind.PEAK.value}{k}"
            if mid in self._markers:
                m = self._markers[mid]
                m.target_hz = f
                m.trace = view.trace
                m.polarity = polarity
            else:
                self._markers[mid] = Marker(kind=Kind.PEAK, number=k, trace=view.trace, target_hz=f, polarity=polarity)
            results.append(OpResult(ST_OK, mid, None, f))
        return results

    # ---- 行の設定（設計G §3.5） ----

    def set_ref(self, marker_id: str, ref_id: Optional[str]) -> None:
        """デルタの基準（なし／他の P・M）。自分自身は選べない。"""
        if ref_id is not None:
            if ref_id == marker_id:
                raise ValueError("自分自身をデルタの基準にはできない")
            if ref_id not in self._markers:
                raise KeyError(ref_id)
        self._markers[marker_id].ref = ref_id

    def set_peak_track(self, marker_id: str, on: bool) -> None:
        """ピーク追従は P だけが持てる（M は周波数を固定: 設計G §3.5）。"""
        m = self._markers[marker_id]
        if on and m.kind != Kind.PEAK:
            raise ValueError("ピーク追従は P だけ")
        m.peak_track = on

    def add_freq_offset(self, marker_id: str, sign: Sign, delta_hz: int) -> None:
        _check_hz("delta_hz", delta_hz)
        if delta_hz <= 0:
            raise ValueError("delta_hz は正の値（向きは sign で決める）")
        self._markers[marker_id].freq_offsets.append(FreqOffset(Sign(sign), delta_hz))

    def add_level_offset(self, marker_id: str, sign: Sign, delta_db: float) -> None:
        if not delta_db > 0:
            raise ValueError("delta_db は正の値（向きは sign で決める）")
        self._markers[marker_id].level_offsets.append(LevelOffset(Sign(sign), float(delta_db)))

    def set_offset(self, marker_id: str, kind: str, index: int, delta) -> None:
        """オフセットのずれを変える（マーカー表のセルで直す: G §3.6）。delta は向き付き（−3 は −Δ の 3）。

        ±Δ のカーソルは ± のまま大きさだけを変える（± は2行に出るので、どちらの行から直しても同じ大きさ）。
        """
        offs = self._markers[marker_id].freq_offsets if kind == "freq" else self._markers[marker_id].level_offsets
        old = offs[index]
        if kind == "freq":
            _check_hz("delta_hz", delta)
        mag = abs(delta) if kind == "freq" else abs(float(delta))
        if not mag > 0:
            raise ValueError("ずれは 0 以外")
        sign = old.sign if old.sign == Sign.BOTH else (Sign.MINUS if delta < 0 else Sign.PLUS)
        offs[index] = (FreqOffset(sign, int(mag)) if kind == "freq" else LevelOffset(sign, float(mag)))

    def remove_freq_offset(self, marker_id: str, index: int) -> None:
        del self._markers[marker_id].freq_offsets[index]

    def remove_level_offset(self, marker_id: str, index: int) -> None:
        del self._markers[marker_id].level_offsets[index]

    def set_bandwidth(self, marker_id: str, n_db: tuple[float, ...]) -> None:
        """N dB 帯域幅の N（空で OFF、1個、2個でシェープファクタも）。"""
        n_db = tuple(float(x) for x in n_db)
        if len(n_db) > 2 or any(not x > 0 for x in n_db):
            raise ValueError("N は正の値を 0〜2 個")
        self._markers[marker_id].bandwidth_n_db = n_db

    def set_threshold(self, marker_id: str, setting: Optional[ThresholdSetting]) -> None:
        if setting is not None and setting.side not in (an.SIDE_BELOW, an.SIDE_ABOVE):
            raise ValueError(f"side が不明: {setting.side}")
        self._markers[marker_id].threshold = setting

    # ---- 掃引ごとの更新 ----

    def update(self, views: Views) -> list[str]:
        """掃引ごとに呼ぶ。ピーク追従の P を近くのピークへ動かし、動いた番号を返す（設計G §3.5）。

        近くのピーク = 盛り上がりの判定を満たすピークのうち、いまの周波数に最も近いもの
        （同じ近さなら高い方）。ピークが無ければ動かさない。範囲の外にいる P は動かさない。
        谷に置いた P（設計G §3.8）は谷を追いかける（同じ近さなら深い方）。
        """
        moved = []
        for m in self.markers():
            if not m.peak_track or m.trace not in views:
                continue
            if views[m.trace].nearest_index(m.target_hz) is None:
                continue
            peaks = an.find_extrema(views[m.trace], self.excursion_db, self.peak_threshold, m.polarity)
            # 谷は値の上下を返したトレースで比べる（「同じ近さなら高い方」が「深い方」になる）
            v = an.oriented(views[m.trace], m.polarity)
            if peaks.size == 0:
                continue
            best = min(
                (int(p) for p in peaks),
                key=lambda p: (abs(v.freq_at(p) - m.target_hz), -v.level_db[p], p),
            )
            f = v.freq_at(best)
            if f != m.target_hz:
                m.target_hz = f
                moved.append(m.id)
        return moved

    # ---- 読み値 ----

    def _read(self, m: Marker, views: Views) -> dict:
        """マーカー1本の読み値（周波数・値・状態・注意）。"""
        v = views.get(m.trace)
        if v is None:
            return {"status": ST_NO_TRACE, "freq_hz": None, "level": None, "unit": None, "idx": None, "notes": []}
        idx = v.nearest_index(m.target_hz)
        if idx is None:
            return {"status": ST_OUT_OF_RANGE, "freq_hz": None, "level": None, "unit": v.unit, "idx": None,
                    "notes": list(v.notes)}
        lv = v.level_at(idx)
        return {
            "status": ST_OK if lv is not None else ST_NO_VALUE,
            "freq_hz": v.freq_at(idx),
            "level": lv,
            "unit": v.unit,
            "idx": idx,
            "notes": list(v.notes) + v.point_flags(idx),
        }

    @staticmethod
    def _delta(r: dict, rr: Optional[dict]) -> dict:
        if rr is None:
            return {"status": ST_NO_REF, "freq_hz": None, "level_db": None}
        if r["status"] != ST_OK or rr["status"] != ST_OK:
            code = ST_REF_UNAVAILABLE if r["status"] == ST_OK else r["status"]
            return {"status": code, "freq_hz": None, "level_db": None}
        df = r["freq_hz"] - rr["freq_hz"]
        if r["unit"] != rr["unit"]:
            # 周波数の差は出せるが、dBm と dB の差は意味を持たないので出さない
            return {"status": ST_UNIT_MISMATCH, "freq_hz": df, "level_db": None}
        return {"status": ST_OK, "freq_hz": df, "level_db": r["level"] - rr["level"]}

    def _freq_offset_rows(self, m: Marker, r: dict, v: Optional[TraceView]) -> list[dict]:
        rows = []
        for i, off in enumerate(m.freq_offsets, start=1):
            for s in off.sign.signs():
                row = {
                    "id": f"{m.id}.f{i}{'+' if s > 0 else '-'}",
                    "sign": off.sign.value,
                    "offset_hz": s * off.delta_hz,
                    "target_hz": None, "freq_hz": None, "level": None,
                    "unit": r["unit"], "delta_level_db": None, "notes": [],
                }
                if r["freq_hz"] is None or v is None:
                    row["status"] = ST_PARENT_UNAVAILABLE
                    rows.append(row)
                    continue
                target = r["freq_hz"] + s * off.delta_hz
                row["target_hz"] = target
                idx = v.nearest_index(target)
                if idx is None:
                    # 範囲の外に出ても消さない（設計G §3.5・U-15）
                    row["status"] = ST_OUT_OF_RANGE
                    rows.append(row)
                    continue
                lv = v.level_at(idx)
                row.update(freq_hz=v.freq_at(idx), level=lv, notes=list(v.notes) + v.point_flags(idx))
                if lv is None:
                    row["status"] = ST_NO_VALUE
                else:
                    row["status"] = ST_OK
                    if r["level"] is not None:
                        row["delta_level_db"] = lv - r["level"]
                rows.append(row)
        return rows

    @staticmethod
    def _level_offset_rows(m: Marker, r: dict, v: Optional[TraceView]) -> list[dict]:
        """レベルのオフセットのカーソル。ずれは dB（設計G §3.5 の ±ΔdB）。表示の形式が |Γ|・SWR のトレースでは、
        元のマーカーの dB からずらした位置を形式の値で出す（設計R §3）。"""
        rows = []
        base_db = an.db_at(v, r["idx"]) if v is not None and r.get("idx") is not None else None
        for i, off in enumerate(m.level_offsets, start=1):
            for s in off.sign.signs():
                d = s * off.delta_db
                if r["level"] is None:
                    level = None
                elif v is not None and v.fmt != an.formats.DB and base_db is not None:
                    level = v.display(base_db + d)
                else:
                    level = r["level"] + d
                rows.append({
                    "id": f"{m.id}.l{i}{'+' if s > 0 else '-'}",
                    "sign": off.sign.value,
                    "offset_db": d,
                    "status": ST_OK if level is not None else ST_PARENT_UNAVAILABLE,
                    "level": level,
                    "unit": r["unit"],
                })
        return rows

    @staticmethod
    def _band_dict(b: an.Band) -> dict:
        return {
            "status": b.status,
            "left_status": b.left.status, "left_hz": b.left.freq_hz,
            "right_status": b.right.status, "right_hz": b.right.freq_hz,
            "width_hz": b.width_hz, "center_hz": b.center_hz, "q": b.q,
        }

    def _functions(self, m: Marker, r: dict, v: Optional[TraceView]) -> tuple[list[dict], Optional[float], Optional[dict]]:
        """マーカーの機能（N dB 帯域幅・しきい値との交点）の結果。"""
        bws: list[dict] = []
        results: list[an.Band] = []
        for n in m.bandwidth_n_db:
            line_level = None
            if v is None or r["idx"] is None:
                b = an.Band(r["status"], an.Edge(r["status"]), an.Edge(r["status"]))
            else:
                b = an.n_db_bandwidth(v, r["idx"], n, m.polarity)  # 谷のマーカーは N dB 上がる点（設計G §3.8）
                base = an.db_at(v, r["idx"])
                if base is not None:
                    # 自動の線（G §3.6）の横線: 探した dB の高さを表示の形式の値で（|Γ|・SWR でも同じ高さ: 設計R §3）
                    line_level = v.display(base + (n if m.polarity == an.DIP else -n))
            results.append(b)
            bws.append({"n_db": n, **self._band_dict(b), "line_level": line_level})
        shape = None
        if len(results) == 2 and all(b.status == an.OK and b.width_hz for b in results):
            # シェープファクタ = 大きい N の帯域幅 ÷ 小さい N の帯域幅（例 60dB ÷ 3dB）
            pairs = sorted(zip(m.bandwidth_n_db, results), key=lambda p: p[0])
            shape = pairs[1][1].width_hz / pairs[0][1].width_hz
        thr = None
        if m.threshold is not None:
            # いまのトレースの表示の形式で言ったしきい値（決めたときの形式と違えば直す: 設計R §3）
            fmt = v.fmt if v is not None else m.threshold.fmt
            level = m.threshold.level_in(fmt, v is None or v.quantity == Quantity.RATIO)
            if v is None or r["idx"] is None:
                b = an.Band(r["status"], an.Edge(r["status"]), an.Edge(r["status"]))
            else:
                b = an.threshold_band(v, r["idx"], level, m.threshold.side)
            shown = level if level == m.threshold.level else round(level, an.formats.threshold_decimals(fmt))
            thr = {"level": shown, "side": m.threshold.side, **self._band_dict(b)}
        return bws, shape, thr

    def snapshot(self, views: Views) -> dict:
        """マーカー表の JSON にできる形（設計G §3.3・§3.5）。文言は含めずコードだけ。

        markers の各行: 番号・種類・トレース・置いた周波数・表示する周波数・値・単位・状態・注意・
        デルタ（基準・Δ周波数・Δ値）・子の行（周波数・レベルのオフセット）・機能の結果。
        """
        reads = {m.id: self._read(m, views) for m in self.markers()}
        rows = []
        for m in self.markers():
            r = reads[m.id]
            v = views.get(m.trace)
            bws, shape, thr = self._functions(m, r, v)
            rows.append({
                "id": m.id,
                "kind": m.kind.value,
                "number": m.number,
                "trace": m.trace,
                "target_hz": m.target_hz,
                "freq_hz": r["freq_hz"],
                "level": r["level"],
                "unit": r["unit"],
                "status": r["status"],
                "notes": r["notes"],
                "polarity": m.polarity,
                "peak_track": m.peak_track,
                "ref": m.ref,
                "delta": None if m.ref is None else self._delta(r, reads.get(m.ref)),
                "freq_offsets": self._freq_offset_rows(m, r, v),
                "level_offsets": self._level_offset_rows(m, r, v),
                "bandwidth": bws,
                "shape_factor": shape,
                "threshold": thr,
            })
        return {"markers": rows, "xy": self._xy_dict(views)}

    def _xy_dict(self, views: Views) -> dict:
        c = self.xy
        dx = c.x2_hz - c.x1_hz if c.x1_hz is not None and c.x2_hz is not None else None
        dy = c.y2 - c.y1 if c.y1 is not None and c.y2 is not None else None
        # X が表示範囲の外にあるか（どのトレースの表示範囲も同じ Start〜Stop なので最初の1本で見る）
        rng = next(iter(views.values()), None)

        def x_status(x: Optional[int]) -> Optional[str]:
            if x is None or rng is None:
                return None
            return ST_OK if rng.start_hz <= x <= rng.stop_hz else ST_OUT_OF_RANGE

        return {
            "x1_hz": c.x1_hz, "x2_hz": c.x2_hz, "dx_hz": dx,
            "x1_status": x_status(c.x1_hz), "x2_status": x_status(c.x2_hz),
            "y1": c.y1, "y2": c.y2, "dy": dy,
        }

    def set_xy(self, **kw: object) -> None:
        """XY カーソルを置く・外す（x1_hz・x2_hz・y1・y2。None で外す）。"""
        for k, val in kw.items():
            if k not in ("x1_hz", "x2_hz", "y1", "y2"):
                raise KeyError(k)
            if k.endswith("_hz") and val is not None:
                _check_hz(k, val)
            if k.startswith("y") and val is not None:
                val = float(val)  # type: ignore[arg-type]
                if not math.isfinite(val):
                    raise ValueError(f"{k} は有限の値")
            setattr(self.xy, k, val)
            if k.startswith("y"):
                setattr(self.xy, k + "_kind", None)  # 置いたときの単位は呼ぶ側が入れる（set_y_kind）
                setattr(self.xy, k + "_ref", None)

    def set_y_kind(self, k: str, kind: Optional[str], ref: Optional[str] = None) -> None:
        """y1・y2 を置いたときの縦軸の単位の種類（Y_KINDS か None）。y が無ければ None。ref は TG の基準との比（"tg"）の
        ときだけ持つ、その基準の id（設計E §6.7）。"""
        if k not in ("y1", "y2") or (kind is not None and kind not in Y_KINDS):
            raise ValueError(f"{k} の単位の種類が不明です: {kind}")
        has = getattr(self.xy, k) is not None
        setattr(self.xy, k + "_kind", kind if has else None)
        setattr(self.xy, k + "_ref", ref if has and kind == Y_KIND_TG else None)

    # ---- マーカーから条件を変える（設計G §3.4） ----

    def center_command(self, marker_id: str, views: Views) -> Command:
        """「この周波数を Center に」。範囲の外・読めないマーカーからは作らない。"""
        r = self._read(self._markers[marker_id], views)
        if r["freq_hz"] is None:
            return Command(r["status"])
        return Command(ST_OK, SweepRequest(center_hz=r["freq_hz"], edited=("center_hz",)))

    def ref_level_command(self, marker_id: str, views: Views) -> Command:
        """「この値を REF レベルに」。値の無い点からは作らない。"""
        r = self._read(self._markers[marker_id], views)
        if r["level"] is None:
            return Command(r["status"])
        return Command(ST_OK, ref_level=r["level"], unit=r["unit"])

    def span_from_delta_command(self, marker_id: str, views: Views) -> Command:
        """「デルタの2点を Start・Stop に」。低い方を Start、高い方を Stop にする。

        この操作の目的は2点の間の幅を見ること（Span を変えることそのもの）なので、Span 固定が ON なら固定を外して
        2点をそのまま Start・Stop にする（固定 ON のドラッグの規則を当てない。外したことは掃引条件モデルが知らせる:
        設計C §5.13）。
        """
        m = self._markers[marker_id]
        if m.ref is None:
            return Command(ST_NO_REF)
        r = self._read(m, views)
        rr = self._read(self._markers[m.ref], views)
        if r["freq_hz"] is None:
            return Command(r["status"])
        if rr["freq_hz"] is None:
            return Command(ST_REF_UNAVAILABLE)
        lo, hi = sorted((r["freq_hz"], rr["freq_hz"]))
        if lo == hi:
            return Command(ST_SAME_FREQUENCY)
        return Command(ST_OK, SweepRequest(start_hz=lo, stop_hz=hi, edited=("start_hz", "stop_hz"), span_lock=False))


# ---------------------------------------------------------------------------
# マーカー1本の置き方の辞書（MarkerTable.to_defs・from_defs と保存ファイルの /markers が使う）
# ---------------------------------------------------------------------------


def marker_to_def(m: Marker) -> dict:
    """マーカー1本の置き方（読み値を含まない）。"""
    return {
        "kind": m.kind.value,
        "number": m.number,
        "trace": m.trace,
        "target_hz": int(m.target_hz),
        "ref": m.ref,
        "peak_track": m.peak_track,
        "freq_offsets": [{"sign": o.sign.value, "delta_hz": int(o.delta_hz)} for o in m.freq_offsets],
        "level_offsets": [{"sign": o.sign.value, "delta_db": float(o.delta_db)} for o in m.level_offsets],
        "bandwidth_n_db": [float(x) for x in m.bandwidth_n_db],
        "threshold": None if m.threshold is None else {
            "level": m.threshold.level, "side": m.threshold.side, "format": m.threshold.fmt, "power": m.threshold.power,
            # TG の基準との比で決めたしきい値だけ、その基準の id（設計E §6.7）
            **({"reference": m.threshold.ref} if m.threshold.ref is not None else {})},
        "polarity": m.polarity,
    }


def marker_from_def(d: dict) -> Marker:
    """marker_to_def の逆。値の形が違えば KeyError・TypeError・ValueError。

    向き（polarity）の無い前の形は山（設計G §3.8 より前のマーカーは山だけ）。
    """
    th = d.get("threshold")
    return Marker(
        polarity=an.check_polarity(d.get("polarity") or an.PEAK),
        kind=Kind(d["kind"]),
        number=int(d["number"]),
        trace=int(d["trace"]),
        target_hz=int(d["target_hz"]),
        ref=d.get("ref"),
        peak_track=bool(d.get("peak_track", False)),
        freq_offsets=[FreqOffset(Sign(o["sign"]), int(o["delta_hz"])) for o in d.get("freq_offsets", [])],
        level_offsets=[LevelOffset(Sign(o["sign"]), float(o["delta_db"])) for o in d.get("level_offsets", [])],
        bandwidth_n_db=tuple(float(x) for x in d.get("bandwidth_n_db", [])),
        threshold=None if th is None else _threshold_from_def(th),
    )


def _threshold_from_def(th: dict) -> ThresholdSetting:
    """しきい値の置き方を戻す。形式の無い前の版は dB、電力かどうかの無い前の版は dB の形式なら電力として扱う
    （前の版は電力の単位を替えると、しきい値を電力として同じだけずらしていた）。"""
    fmt = th.get("format") if an.formats.is_format(th.get("format")) else an.formats.DB
    power = th["power"] if isinstance(th.get("power"), bool) else fmt == an.formats.DB
    ref = th.get("reference") if isinstance(th.get("reference"), str) and not power else None  # 設計E §6.7
    return ThresholdSetting(float(th["level"]), str(th["side"]), fmt, power, ref)
