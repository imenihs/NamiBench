"""平均の種類と、平均を取る領域への行き来（設計F §5.2・§5.2.1）。

トレースの値はリニアで持つ（電力は W、比はリニアの振幅比）。平均は種類ごとに決まった領域で取り、
結果をリニアへ戻す。
- パワー平均: 電力の領域（電力は W のまま、比は |x|²）
- 電圧平均: 振幅の領域（電力は √W、比は |x|）
- 対数平均: dB の領域（10·log10 of 電力の領域）
- ベクトル平均: 複素数のまま（複素数の比だけ）
純粋な計算だけ。
"""
from __future__ import annotations

import enum

import numpy as np

from ..types import Quantity, ValueKind


class AverageType(str, enum.Enum):
    """平均の種類（設計F §5.2.1）。"""

    POWER = "power"
    LOG = "log"
    VOLTAGE = "voltage"
    VECTOR = "vector"


# 対数平均を選んだときにその場で出す知らせのコード（U-02）。ノイズの電力が約 2.5dB 低く読める
# ことを伝える。文言は画面側が決める
NOTICE_LOG_AVERAGE_READS_LOW = "log_average_reads_low"

# 対数平均で 0 W の点を dB にすると −inf になり、指数平均の更新で nan になる。
# それを避けるため、dB にする前に正の最小の正規化数で下を切る（設計に無い判断。値としては
# 約 −3080dB で、表示に出る値を変えない）
_LOG_FLOOR = np.finfo(np.float64).tiny


def allowed_average_types(value_kind: ValueKind) -> tuple[AverageType, ...]:
    """選べる平均の種類。ベクトル平均は複素数のときだけ、複素数はベクトル平均だけ（設計F §5.2.1）。"""
    if value_kind == ValueKind.COMPLEX:
        return (AverageType.VECTOR,)
    return (AverageType.POWER, AverageType.LOG, AverageType.VOLTAGE)


def default_average_type(quantity: Quantity, value_kind: ValueKind) -> AverageType:
    """既定の平均の種類: 電力はパワー平均、実数の比は電圧平均、複素数はベクトル平均（設計F §5.2）。"""
    if value_kind == ValueKind.COMPLEX:
        return AverageType.VECTOR
    if quantity == Quantity.RATIO:
        return AverageType.VOLTAGE
    return AverageType.POWER


def _power_domain(values: np.ndarray, quantity: Quantity) -> np.ndarray:
    """電力の領域の値（電力は W そのもの、比は |x|²）。"""
    if quantity == Quantity.POWER:
        return np.abs(values).astype(np.float64)
    return np.abs(values).astype(np.float64) ** 2


def _from_power_domain(p: np.ndarray, quantity: Quantity) -> np.ndarray:
    if quantity == Quantity.POWER:
        return p
    return np.sqrt(p)


def to_domain(values: np.ndarray, quantity: Quantity, kind: AverageType) -> np.ndarray:
    """リニアの値を、平均を取る領域へ移す。"""
    if kind == AverageType.VECTOR:
        return np.asarray(values, dtype=np.complex128).copy()
    p = _power_domain(np.asarray(values), quantity)
    if kind == AverageType.POWER:
        return p
    if kind == AverageType.VOLTAGE:
        return np.sqrt(p)
    with np.errstate(invalid="ignore"):
        return 10.0 * np.log10(np.maximum(p, _LOG_FLOOR))


def from_domain(acc: np.ndarray, quantity: Quantity, kind: AverageType) -> np.ndarray:
    """平均を取った領域の値をリニアへ戻す。"""
    if kind == AverageType.VECTOR:
        return acc.copy()
    if kind == AverageType.POWER:
        p = acc
    elif kind == AverageType.VOLTAGE:
        p = acc ** 2
    else:
        p = np.power(10.0, acc / 10.0)
    return _from_power_domain(p, quantity)
