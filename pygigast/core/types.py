"""コアの共通の型。

周波数は Hz の int で持ち、float で持たない・計算しない（設計A §2.1）。
機器に固有の値（GigaSt の 100Hz 単位・AD 値など）はここに持ち込まない（設計Q §2）。
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


class Mode(str, enum.Enum):
    """測定のモード（設計L §1.1）。"""

    SA = "sa"
    TG = "tg"
    S11 = "s11"
    SG = "sg"
    NF = "nf"


class Quantity(str, enum.Enum):
    """トレースの量の種類（設計F §2）。平均の取り方が変わる。"""

    POWER = "power"
    RATIO = "ratio"


class ValueKind(str, enum.Enum):
    """値が実数か複素数か（設計Q §2.1・設計F §2）。"""

    REAL = "real"
    COMPLEX = "complex"


@dataclass(frozen=True)
class FreqAxis:
    """等間隔の周波数軸。i 番目の点は start_hz + i * step_hz。"""

    start_hz: int
    step_hz: int
    points: int

    def __post_init__(self) -> None:
        if not isinstance(self.start_hz, int) or not isinstance(self.step_hz, int):
            raise TypeError("周波数は int（Hz）で持つ")
        if self.points < 1:
            raise ValueError("points は 1 以上")
        if self.step_hz < 0:
            raise ValueError("step_hz は 0 以上")

    def freqs(self) -> np.ndarray:
        """各点の周波数（int64 の配列）。"""
        return self.start_hz + self.step_hz * np.arange(self.points, dtype=np.int64)

    @property
    def last_hz(self) -> int:
        return self.start_hz + self.step_hz * (self.points - 1)


@dataclass(frozen=True)
class Adjustment:
    """求めた値を機器が受け付ける値に変えた記録（設計C §2・設計A §4.3）。

    reason は理由の種類のコード。画面の文言は画面側が決める（U-22）。
    """

    field: str
    requested: object
    adopted: object
    reason: str


@dataclass(frozen=True)
class SweepRequest:
    """利用者が求めた掃引条件（設計C §5.1）。

    変えたい欄だけを入れ、edited に「触った欄」を欄の名前（"start_hz" など）で並べる。
    入っていない欄（None）は前の条件を引き継ぐ。
    """

    start_hz: Optional[int] = None
    stop_hz: Optional[int] = None
    center_hz: Optional[int] = None
    span_hz: Optional[int] = None
    edited: tuple[str, ...] = ()
    rbw_preference_hz: Optional[int] = None
    points: Optional[int] = None  # None は引き継ぐ、0 は自動に戻す（設計C §5.3）
    mode: Optional[Mode] = None
    sg_hz: Optional[int] = None
    image_cancel: Optional[bool] = None
    # Span 固定の ON/OFF（None は引き継ぐ）。ON にした時点の Span が固定 Span になる（設計C §5.11）
    span_lock: Optional[bool] = None
    # その1回だけの命令: 固定 Span を変えない（設計C §5.11 の 3）
    one_shot: bool = False


@dataclass(frozen=True)
class Alternative:
    """規則で値を動かしたとき、反対の規則で作った結果（設計C §5.11 の 3）。"""

    rule: str  # "keep_span" | "keep_center"
    start_hz: int
    stop_hz: int


@dataclass(frozen=True)
class AdoptedSweep:
    """機器が受け付ける形に直した掃引条件（設計C §5.8）。

    start_hz / stop_hz は利用者が指定した範囲（表示・保存はここで切る）。
    axis は機器に測らせる点（前置きを含まない。Stop を超える最初の格子点まで: 設計C §5.3）。
    機器に固有の値（GigaSt の Sample など）は持たない。機器の層が計算する。
    """

    start_hz: int
    stop_hz: int
    axis: FreqAxis
    rbw_hz: int
    rbw_preference_hz: int
    mode: Mode
    sg_hz: Optional[int]
    image_cancel: bool
    preroll_points: int
    transient_head_points: int
    estimated_sweep_s: float
    adjustments: tuple[Adjustment, ...] = ()
    # 利用者が選んだ点数（None は自動）。次の条件変更で引き継ぐ（設計C §5.3 提案2）
    points_setting: Optional[int] = None
    # 最後の点を機器の上限のため削り、Stop の手前 Step 未満にデータが無い（設計C §5.3）
    tail_gap: bool = False
    # 信用できない条件の知らせ（止めない。設計C §5.3 の2）。コードで持ち、文言は画面側
    warnings: tuple[str, ...] = ()
    # 固定 Span（Span 固定が OFF のときは None。設計C §5.11）
    span_lock_hz: Optional[int] = None
    # この適用で規則により値を動かしたときの反対の結果（無ければ None）
    alternative: Optional[Alternative] = None

    @property
    def center_hz(self) -> int:
        return self.start_hz + (self.stop_hz - self.start_hz) // 2

    @property
    def span_hz(self) -> int:
        return self.stop_hz - self.start_hz

    @property
    def zero_span(self) -> bool:
        return self.axis.step_hz == 0


@dataclass(frozen=True)
class DeviceIdentity:
    """つながっている機器の身元。"""

    model: str
    serial: str
    port: str


@dataclass
class RawSweep:
    """機器が返した1回分の生の値（設計B §7.1）。

    raw は機器ごとの形（encoding で区別）。GigaSt は AD 値の uint16。
    raw_b3 はイメージキャンセルの b3=0 側の生の値（ON のときだけ）。raw は採用した値（min 済み）。
    NF の HOT/COLD は raw_hot / raw_cold に入れ、raw は HOT とする（設計M）。
    """

    adopted: AdoptedSweep
    raw: np.ndarray
    encoding: str
    started_at: float
    duration_s: float
    raw_b3_0: Optional[np.ndarray] = None
    raw_b3_1: Optional[np.ndarray] = None
    raw_hot: Optional[np.ndarray] = None
    raw_cold: Optional[np.ndarray] = None
    transient_head_points: int = 0
    extra: dict = field(default_factory=dict)


@dataclass
class Frame:
    """1掃引分の換算済みの値（トレース・マーカー・ノーマライズ・保存が受け取る形。設計F §5.1）。

    values はリニア: 電力は W（float64）、比はリニアの振幅比（実数は float64、複素数は complex128）。
    axis は機器が測った点（Stop を超える最後の点を含みうる）。表示・マーカー・保存・書き出しは
    display_start_hz〜display_stop_hz で切る（設計C §5.3）。
    flags は点ごとの印（bool の配列）: "transient"（前置き不足）・"out_of_cal_range"（補正の範囲外）・
    "no_reference"（ノーマライズの基準が無い点）など。
    profile は測った機器の機種のプロファイルの写し（設計Q §2.1）。
    """

    axis: FreqAxis
    values: np.ndarray
    quantity: Quantity
    value_kind: ValueKind
    time: float
    duration_s: float
    sweep_no: int
    display_start_hz: int
    display_stop_hz: int
    adopted: Optional[AdoptedSweep] = None
    raw: Optional[RawSweep] = None
    flags: dict = field(default_factory=dict)
    profile: dict = field(default_factory=dict)


class DeviceError(Exception):
    """機器との通信の失敗の基底。"""


class DeviceDisconnected(DeviceError):
    """機器が切れた（読み書きの例外・ポートが消えた）。"""


class PortInUse(DeviceDisconnected):
    """ポートをほかのアプリが開いている（設計J §4.7）。GigaSt の応答が無いのとは別の理由として扱う。"""


class SweepDiscarded(DeviceError):
    """その掃引は使えないので捨てた（バイト数の不足・上限時間の超過）。次の掃引へ進んでよい。"""

    def __init__(self, reason: str, detail: Optional[dict] = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail or {}


class InvalidSweep(DeviceError):
    """機器が受け付けない条件が送信層まで届いた（設計B §2 の5: 二重の防御）。送らない。"""
