"""トレースの表示の形式（設計R §3・§5.1・設計E §6.5）。

値（比のトレースはリニアの振幅比 |Γ|）は変えず、画面・マーカー・CSV・PNG に出すときの表し方だけを決める。

- db: 振幅（dB）。比は 20·log10|v|。S11 では「測った値 ÷ 基準」の dB（設計E §6.5）で、全反射が 0dB、整合するほど
  負に深い（共振は谷）。名前は S11 では「リターンロス（dB）」、S21 では「振幅（dB）」（画面の語: texts）
- gamma: |Γ|（反射係数の大きさ。リニア）。S11 の比だけ
- swr: SWR = (1+|Γ|)/(1−|Γ|)。S11 の比だけ。|Γ| が 1 に近い・1 以上（ばらつきで基準を超えた点）は SWR_MAX にそろえ、
  点の印 swr_limit を付ける（無限大を作らない: 読み値・CSV・JSON で数として扱えるように）
- phase・group_delay・smith・polar: 位相を測れる機器（複素数の値）だけ（設計R §2・§5.1）。GigaSt（振幅だけ）では
  選べない。画面の部品は最初から置き、無効にして理由を出す（R §5.1）

|Γ|・SWR は |Γ| について単調に増えるので、ピーク・谷と盛り上がりの判定・N dB 帯域幅は dB のまま探し（探す点は
形式で変わらない）、読み値だけを形式で出す。しきい値は形式の値で受け、dB に直して探す。

縦軸（設計J §4.1 を |Γ|・SWR に広げたもの）: dB の形式は REF レベル（一番上）と dB/div の2つ。|Γ|・SWR は下端を
決まった値（SWR 1・|Γ| 0）に置き、1目盛の大きさ（/div）だけを選ぶ（反射のない点が必ず下端に来る。SWR 1 より下・
|Γ| 0 より下は存在しないので、REF を動かして下に空きを作る使い道が無い）。
純粋な計算だけ。Qt・スレッド・入出力を持たない。
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from ..types import Mode, Quantity

DB = "db"
GAMMA = "gamma"
SWR = "swr"
PHASE = "phase"
GROUP_DELAY = "group_delay"
SMITH = "smith"
POLAR = "polar"

# 選択肢の並び（画面の一覧の順）
FORMATS = (DB, GAMMA, SWR, PHASE, GROUP_DELAY, SMITH, POLAR)
# 位相を測れる機器（複素数の値）だけで使える形式
VECTOR_FORMATS = (PHASE, GROUP_DELAY, SMITH, POLAR)
# 反射（S11）の比のトレースだけで使える形式
S11_FORMATS = (GAMMA, SWR)

UNIT_GAMMA = "|Γ|"
UNIT_SWR = "SWR"
FORMAT_UNITS = {GAMMA: UNIT_GAMMA, SWR: UNIT_SWR}
# 縦軸が dB でない（下端を決めて 1目盛の大きさだけを選ぶ）単位
LINEAR_UNITS = (UNIT_GAMMA, UNIT_SWR)

# SWR の上限（これより大きい値・|Γ| が 1 以上の点はこの値にして印を付ける）。アンテナの整合で読むのは 1〜数で、
# 方向性結合器を使うスカラーの測定では大きな SWR の値の差は意味を持たない
SWR_MAX = 100.0
_GAMMA_AT_SWR_MAX = (SWR_MAX - 1.0) / (SWR_MAX + 1.0)
FLAG_SWR_LIMIT = "swr_limit"

# |Γ|・SWR の縦軸: 下端・1目盛の大きさの選択肢・初期値・状態の amplitude に持つ名前
LINEAR_AXES = {
    UNIT_SWR: {"bottom": 1.0, "choices": (0.05, 0.1, 0.2, 0.5, 1.0, 2.0), "default": 0.2, "key": "swr_per_div"},
    UNIT_GAMMA: {"bottom": 0.0, "choices": (0.01, 0.02, 0.05, 0.1), "default": 0.1, "key": "gamma_per_div"},
}


def is_format(code) -> bool:
    return isinstance(code, str) and code in FORMATS


def applies(fmt: str, mode, quantity) -> bool:
    """形式がそのトレースの値に当たるか。|Γ|・SWR は S11 の比だけ。位相系はここでは当たらない（実数の値）。"""
    if fmt == DB:
        return True
    if fmt in S11_FORMATS:
        return quantity == Quantity.RATIO and _mode(mode) == Mode.S11
    return False


def effective(setting: Optional[str], mode, quantity) -> str:
    """いま使う形式。選んだ形式が当たらない（S11 の比でない・位相系）ときは dB（選んだものは覚えておく）。"""
    s = setting if is_format(setting) else DB
    return s if applies(s, mode, quantity) else DB


def unit_of(fmt: str, base_unit: Optional[str]) -> Optional[str]:
    """形式で出す単位。dB の形式は元の単位（dBm・dB など）のまま。"""
    return FORMAT_UNITS.get(fmt, base_unit)


def _mode(mode) -> Optional[Mode]:
    if isinstance(mode, Mode) or mode is None:
        return mode
    try:
        return Mode(mode)
    except ValueError:
        return None


def from_db(level_db, fmt: str) -> np.ndarray:
    """dB（20·log10|Γ|）の値を形式の値にする。値なし（NaN）は NaN のまま。"""
    v = np.asarray(level_db, dtype=np.float64)
    if fmt not in S11_FORMATS:
        return v
    with np.errstate(over="ignore", invalid="ignore"):
        g = np.power(10.0, v / 20.0)
    if fmt == GAMMA:
        return g
    out = np.full(g.shape, np.nan)
    ok = np.isfinite(g)
    under = ok & (g < _GAMMA_AT_SWR_MAX)
    out[under] = (1.0 + g[under]) / (1.0 - g[under])
    out[ok & ~under] = SWR_MAX
    return out


def value_from_db(level_db: Optional[float], fmt: str) -> Optional[float]:
    if level_db is None or not math.isfinite(level_db):
        return None
    return float(from_db(np.array([level_db]), fmt)[0])


def to_db(value: float, fmt: str) -> float:
    """形式の値を dB に戻す（しきい値・レベルのカーソルを dB で探すため）。範囲の外は端の dB にする。"""
    v = float(value)
    if fmt == GAMMA:
        return 20.0 * math.log10(v) if v > 0 else -math.inf
    if fmt == SWR:
        if v <= 1.0:
            return -math.inf
        g = (v - 1.0) / (v + 1.0)
        return 20.0 * math.log10(g)
    return v


def threshold_decimals(fmt: str) -> int:
    """しきい値の小数の桁（dB は 0.1dB: G §3.6、|Γ|・SWR は 0.01）。"""
    return 2 if fmt in S11_FORMATS else 1


# 下端より下（SWR 1 以下・|Γ| 0 以下）を dB に直したときの値（無限大を作らない。しきい値の欄の下限と同じ）
_FLOOR_VALUE = {DB: -300.0, GAMMA: 0.0, SWR: 1.0}


def convert_value(value: float, from_fmt: str, to_fmt: str) -> float:
    """ある形式の値（しきい値など）を、同じ反射の大きさの別の形式の値にする（例 SWR 2 → |Γ| 0.333 → −9.54dB）。"""
    if from_fmt == to_fmt or from_fmt not in (DB, GAMMA, SWR) or to_fmt not in (DB, GAMMA, SWR):
        return float(value)
    db = to_db(value, from_fmt)
    if not math.isfinite(db):
        return _FLOOR_VALUE[to_fmt]
    out = value_from_db(db, to_fmt)
    return _FLOOR_VALUE[to_fmt] if out is None else out


def swr_limit_mask(level_db, fmt: str) -> Optional[np.ndarray]:
    """SWR の上限に当てた点（|Γ| が SWR_MAX に当たる大きさ以上）。SWR の形式でなければ None。"""
    if fmt != SWR:
        return None
    v = np.asarray(level_db, dtype=np.float64)
    with np.errstate(over="ignore", invalid="ignore"):
        g = np.power(10.0, v / 20.0)
    return np.isfinite(g) & (g >= _GAMMA_AT_SWR_MAX)


# ---------------------------------------------------------------- 縦軸と数の書き方（Qt・Web・PNG で同じ）

def is_linear_unit(unit: Optional[str]) -> bool:
    return unit in LINEAR_UNITS


def per_div_of(unit: str, amplitude: Optional[dict]) -> float:
    """|Γ|・SWR の縦軸の1目盛の大きさ（状態の amplitude に無い・選択肢に無い値なら初期値）。"""
    ax = LINEAR_AXES[unit]
    v = (amplitude or {}).get(ax["key"])
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and float(v) in ax["choices"] \
        else float(ax["default"])


def linear_top(unit: str, per_div: float, divisions: int) -> float:
    """|Γ|・SWR の縦軸の一番上の値（下端 + 目盛の数 × 1目盛）。"""
    return LINEAR_AXES[unit]["bottom"] + divisions * per_div


def decimals_for(step: float) -> int:
    """1目盛 step を書き分けられる小数の桁（0.05 → 2、0.2 → 1、1 → 0）。"""
    for d in range(0, 4):
        if abs(round(step, d) - step) < 1e-9:
            return d
    return 3


def tick_decimals(unit: Optional[str], per_div: float) -> int:
    """縦軸の目盛の数字の小数の桁。dB 系は 0.1dB（REF の欄と同じ）、|Γ|・SWR は1目盛を書き分けられる桁（少なくとも1桁）。"""
    if not is_linear_unit(unit):
        return 1
    return max(1, decimals_for(per_div))


def value_decimals(unit: Optional[str]) -> int:
    """読み値（マーカー・マウスの位置・PNG の表）の小数の桁。|Γ| は 0〜1 の値なので3桁、ほかは2桁。"""
    return 3 if unit == UNIT_GAMMA else 2


def value_cell(v: Optional[float], unit: Optional[str]) -> str:
    """マーカー表の値の文字（画面の表・コピー・PNG の表で同じ）。"""
    if v is None:
        return ""
    return f"{v:.{value_decimals(unit)}f} {unit or ''}".rstrip()


def delta_cell(dv: Optional[float], unit: Optional[str], db_offset: bool = False) -> str:
    """マーカー表の Δ値の文字。列の名前は dB なので、|Γ|・SWR の差には単位を添える。db_offset はレベルのカーソルの
    ずれ（dB）。"""
    if dv is None:
        return ""
    if not db_offset and is_linear_unit(unit):
        return f"{dv:+.{value_decimals(unit)}f} {unit}"
    return f"{dv:+.2f}"
