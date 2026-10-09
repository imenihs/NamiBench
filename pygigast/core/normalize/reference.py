"""ノーマライズの基準（設計E §6.1・§6.2）。

基準は「区間の並び」。区間ごとに等間隔の周波数軸（Hz の int）と各点の受けた電力（W、リニア）と、
取ったときの条件を持つ。旧アプリのような「そのときの表示の点」ではなく周波数の関数として持つので、
スパン・センターを変えても使い続けられる（設計E §2）。
- 使えない点（前置き不足など）は NaN で持つ。NaN の点は基準として使わない
- 新しい区間を足すと、同じ条件の既存の区間の、新しい区間の範囲に入る点を捨てる（上書き合成。範囲の外は残る）
- 基準の値（value_db。設計E §6.7）: 基準の経路が持つ既知の利得・損失（ATT 40dB を入れて取ったなら +40）。
  表示 = 基準との比 + 基準の値。基準ごとに1つ持ち、取った後でも直せる（測り直さない）。S11 の基準は全反射が 0dB と
  決まっているので持たない（いつも 0）
- 純粋な計算だけ。スレッド・入出力を持たない（保存は store.py）
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from typing import Optional, Sequence

import numpy as np

from ..types import FreqAxis, Frame, Mode, Quantity

# 区間の取り方（設計E §6.2）。精度の印は取り方ではなく Step の一致で決める（設計E §6.3）
KIND_WIDE = "wide"  # 「広く取る」: TG の全範囲を 1MHz 刻みで
KIND_FINE = "fine"  # 「いまの条件で細かく取る」: いまの Start・Stop・Step で

# 「広く取る」の Step（設計E §2・§6.2）
WIDE_STEP_HZ = 1_000_000

# 基準の値の範囲（dB。設計E §6.7）。アッテネータ・アンプの利得として意味のある広さ（設計に値は無い。打ち間違いで
# 画面が使えなくなるのを防ぐ）
VALUE_DB_LIMIT = 100.0


class ReferenceBuildError(ValueError):
    """基準を作れない（フレームの条件がそろわない・電力でない など）。"""


def check_value_db(v: object) -> float:
    """基準の値（dB）を確かめて float で返す。数でない・有限でない・範囲（±VALUE_DB_LIMIT）の外は ValueError。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not np.isfinite(v):
        raise ValueError("基準の値は有限の数")
    if abs(float(v)) > VALUE_DB_LIMIT:
        raise ValueError(f"基準の値は ±{VALUE_DB_LIMIT:g} dB の中")
    return float(v)


@dataclass(frozen=True, eq=False)
class ReferenceSegment:
    """基準の1区間（設計E §6.1）。

    power_w は axis の各点の受けた電力（W、float64）。NaN の点は使わない。
    image_reject（実際に使ったイメージ除去 "accurate"・"fast"・"off"）・rbw_hz は当てるときに測定と一致を求める
    条件（設計E §6.3・設計F §7.3）。
    connected_at は取ったときの接続（＝電源投入）の時刻。いまの接続と違えば「電源の入れ直し前」（設計E §4.1）。
    """

    axis: FreqAxis
    power_w: np.ndarray
    image_reject: str
    rbw_hz: int
    connected_at: float
    taken_at: float
    kind: str = KIND_FINE
    averages: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.rbw_hz, int) or isinstance(self.rbw_hz, bool):
            raise TypeError("rbw_hz は int（Hz）で持つ")
        p = np.asarray(self.power_w, dtype=np.float64)
        if p.shape != (self.axis.points,):
            raise ValueError("power_w の点数が軸と合わない")
        object.__setattr__(self, "power_w", p)

    def freqs(self) -> np.ndarray:
        return self.axis.freqs()

    def same_conditions(self, other: "ReferenceSegment") -> bool:
        """上書き合成の相手になる（当てるときの条件が同じ）か。"""
        return self.image_reject == other.image_reject and self.rbw_hz == other.rbw_hz


@dataclass
class Reference:
    """名前の付いた基準（設計E §4・§6.1）。

    mode は TG（通過）か S11（反射）。band_edges_hz は取った機器の出力の帯の境目
    （機種のプロファイル source.band_edges_hz の写し。設計E §4.1）で、ここをまたいで補間しない。
    value_db は基準の値（設計E §6.7: 表示 = 基準との比 + 基準の値）。S11 では当てない（全反射 = 0dB）。
    """

    name: str
    model: str
    serial: str
    mode: Mode
    band_edges_hz: tuple[int, ...] = ()
    segments: list[ReferenceSegment] = field(default_factory=list)
    ref_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    value_db: float = 0.0

    def __post_init__(self) -> None:
        for e in self.band_edges_hz:
            if not isinstance(e, int) or isinstance(e, bool):
                raise TypeError("band_edges_hz は int（Hz）で持つ")
        self.band_edges_hz = tuple(sorted(self.band_edges_hz))
        self.value_db = check_value_db(self.value_db)

    @property
    def applied_value_db(self) -> float:
        """当てるときに比へ足す dB。S11 の基準は全反射が 0dB と決まっているので当てない（設計E §6.7）。"""
        return self.value_db if self.mode == Mode.TG else 0.0

    @property
    def taken_at(self) -> Optional[float]:
        """最後に区間を足した日時（区間が無ければ None）。"""
        return max((s.taken_at for s in self.segments), default=None)

    def merge(self, segment: ReferenceSegment) -> None:
        """区間を上書き合成する（設計E §6.2）。

        同じ条件（イメージ除去・RBW）の既存の区間から、新しい区間の範囲
        [start, last] に入る点を捨て、新しい区間を足す。範囲の外の点は残る。
        範囲の境目の隙間は当てるときに隣の区間の点どうしで補間する（apply.py）。
        """
        lo, hi = segment.axis.start_hz, segment.axis.last_hz
        kept: list[ReferenceSegment] = []
        for s in self.segments:
            if not s.same_conditions(segment):
                kept.append(s)
                continue
            kept.extend(_cut_outside(s, lo, hi))
        kept.append(segment)
        self.segments = kept


def _cut_outside(seg: ReferenceSegment, lo: int, hi: int) -> list[ReferenceSegment]:
    """seg のうち [lo, hi] の外の点を、下側と上側の区間（0〜2個）として返す。"""
    f = seg.freqs()
    out: list[ReferenceSegment] = []
    below = np.nonzero(f < lo)[0]
    above = np.nonzero(f > hi)[0]
    for idx in (below, above):
        if idx.size == 0:
            continue
        i0, n = int(idx[0]), int(idx.size)
        axis = FreqAxis(int(f[i0]), seg.axis.step_hz, n)
        out.append(replace(seg, axis=axis, power_w=seg.power_w[i0 : i0 + n].copy()))
    return out


def _frame_conditions(frame: Frame) -> tuple[Mode, str, int]:
    """(モード, 実際に使ったイメージ除去, RBW)。イメージ除去はゼロスパンでは選択によらず "off"（設計F §7.1）。"""
    if frame.adopted is None:
        raise ReferenceBuildError("フレームに掃引条件が無い（モード・イメージ除去・RBW が分からない）")
    a = frame.adopted
    return a.mode, a.image_reject_used, a.rbw_hz


def average_power(frames: Sequence[Frame]) -> np.ndarray:
    """同じ軸・同じ条件の電力のフレームを W のまま平均する（パワー平均。設計F §5.2.1 の既定）。

    前置き不足（flags["transient"]）の点は平均に入れない。全フレームで使えない点は NaN。
    """
    if not frames:
        raise ReferenceBuildError("フレームが無い")
    first = frames[0]
    cond = _frame_conditions(first)
    stack = []
    for fr in frames:
        if fr.quantity != Quantity.POWER:
            raise ReferenceBuildError("基準は受けた電力のフレームから取る")
        if fr.axis != first.axis:
            raise ReferenceBuildError("フレームの周波数軸がそろわない")
        if _frame_conditions(fr) != cond:
            raise ReferenceBuildError("フレームの条件（モード・イメージ除去・RBW）がそろわない")
        v = np.array(np.real(fr.values), dtype=np.float64)
        tr = fr.flags.get("transient")
        if tr is not None:
            v[np.asarray(tr, dtype=bool)] = np.nan
        stack.append(v)
    arr = np.vstack(stack)
    ok = ~np.isnan(arr)
    cnt = ok.sum(axis=0)
    total = np.where(ok, arr, 0.0).sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(cnt > 0, total / np.maximum(cnt, 1), np.nan)


def segment_from_frames(
    frames: Sequence[Frame],
    *,
    connected_at: float,
    taken_at: float,
    kind: str = KIND_FINE,
    expected_mode: Mode = Mode.TG,
) -> ReferenceSegment:
    """被測定物を外して TG の経路を測ったフレーム（1回以上）から基準の区間を作る（設計E §6.2）。

    複数のフレームは W のまま平均する。軸は機器が測った点をそのまま使う
    （Stop を超える最後の点も実際に測った点なので基準に入れる）。
    """
    power = average_power(frames)
    mode, image_reject, rbw_hz = _frame_conditions(frames[0])
    if mode != expected_mode:
        raise ReferenceBuildError(f"モードが {expected_mode.value} でない（{mode.value}）")
    return ReferenceSegment(
        axis=frames[0].axis,
        power_w=power,
        image_reject=image_reject,
        rbw_hz=rbw_hz,
        connected_at=connected_at,
        taken_at=taken_at,
        kind=kind,
        averages=len(frames),
    )


def new_reference(name: str, profile: dict, serial: str, mode: Mode = Mode.TG, value_db: float = 0.0) -> Reference:
    """空の基準を作る。帯の境目は機種のプロファイルから写す（設計Q §2.1・設計E §4.1）。value_db は基準の値（設計E §6.7）。"""
    edges = tuple(int(e) for e in profile.get("source", {}).get("band_edges_hz", []))
    return Reference(
        name=name, model=str(profile.get("model", "")), serial=serial, mode=mode, band_edges_hz=edges,
        value_db=value_db,
    )


def wide_range_hz(profile: dict) -> tuple[int, int, int]:
    """「広く取る」の (start_hz, stop_hz, step_hz)（設計E §6.2）。

    範囲は機種のプロファイルの source.tg_range_hz（[下限, 上限]）。機器に固有の値をここに持たないため、
    プロファイルに無ければ KeyError にする。
    """
    lo, hi = profile["source"]["tg_range_hz"]
    return int(lo), int(hi), WIDE_STEP_HZ
