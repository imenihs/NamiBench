"""AD 値から dBm への換算（設計D §5.3・§5.5）。

SA・SG: P = AD × 80/1986 − 80 + level_offset_db + rbw_offset_db[実際の RBW] + corr(f)
TG・S11: P = AD × 80/1986 − 80 + level_offset_db（周波数特性と RBW の補正はノーマライズが兼ねる: 設計E）
NF: ここでは換算しない。HOT と COLD の組から level/nf.py が NF を出す（設計M。補正は当てない: 設計D §3）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..types import AdoptedSweep, Mode
from .calibration import Calibration

# 換算できる生の値の形（機種のプロファイルの raw.encoding）
_ENCODING_GIGAST_AD_U16 = "gigast_ad_u16"


@dataclass(frozen=True)
class ConvertedLevels:
    """換算の結果。

    out_of_cal_range は点ごとの「補正の範囲外」の印（f < 0、または周波数特性補正の表の外）。
    no_calibration は校正データが無く補正なしで換算したこと（状態表示に出す: 設計D §5.5）。
    rbw_offset_missing は校正データに実際の RBW の補正が無く、0 で換算したこと。
    """

    dbm: np.ndarray
    out_of_cal_range: np.ndarray
    no_calibration: bool
    rbw_offset_missing: bool = False


class LevelConverter:
    """機種のプロファイルと個体の校正から、生の値を dBm にする。"""

    def __init__(self, profile: dict, cal: Optional[Calibration]) -> None:
        raw = profile["raw"]
        if raw.get("encoding") != _ENCODING_GIGAST_AD_U16:
            raise NotImplementedError(f"換算できない生の値の形: {raw.get('encoding')!r}")
        self._counts_per_80db = float(raw["ad_counts_per_80db"])
        self._cal = cal
        if cal is not None:
            self._table = np.array([b.values for b in cal.bands], dtype=np.float64)
            self._step_hz = int(cal.freq_step_hz)
            self._band_span_hz = int(cal.band_span_hz)
        else:
            self._table = None

    @property
    def calibration(self) -> Optional[Calibration]:
        return self._cal

    def ad_to_db(self, raw: np.ndarray) -> np.ndarray:
        """補正の前の値（AD 値 × 80/1986 − 80）。"""
        # uint16 のまま ×80 すると桁あふれするので、先に float にする（設計D §5.3・T10）
        ad = np.asarray(raw).astype(np.float64)
        return ad * 80.0 / self._counts_per_80db - 80.0

    def corr(self, freqs_hz: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """周波数特性の補正と「補正の範囲外」の印（設計D §5.3）。

        band と区間の番号は Hz の整数で求め、区間の中の位置（frac）だけ実数にする。
        band の境目ちょうどでは次の band の最初の値を使う（旧アプリと同じ）。
        """
        f = np.asarray(freqs_hz)
        if f.dtype.kind not in "iu":
            raise TypeError("周波数は整数（Hz）の配列で渡す")
        f = f.astype(np.int64)
        out = np.zeros(f.shape, dtype=np.float64)
        flag = np.zeros(f.shape, dtype=bool)
        if self._table is None:
            return out, flag

        nbands = self._table.shape[0]
        neg = f < 0
        b = f // self._band_span_hz
        above = (~neg) & (b >= nbands)
        inside = ~(neg | above)

        fi = f[inside]
        bi = b[inside]
        pos = fi - bi * self._band_span_hz
        k = pos // self._step_hz  # 0〜9
        frac = (pos - k * self._step_hz).astype(np.float64) / self._step_hz
        v0 = self._table[bi, k]
        v1 = self._table[bi, k + 1]
        out[inside] = v0 + (v1 - v0) * frac

        # f < 0: band 0 の最初の値を使い、印を付ける（旧アプリは前回の値が残る不定の動き）
        out[neg] = self._table[0, 0]
        flag[neg] = True
        # 表の外（12000MHz 以上）: 補正 0（旧アプリと同じ）、印を付ける
        flag[above] = True
        return out, flag

    def to_dbm(self, adopted: AdoptedSweep, raw: np.ndarray) -> ConvertedLevels:
        """1回分の生の値を dBm にする。raw は adopted.axis の点と同じ数。"""
        raw = np.asarray(raw)
        if raw.shape != (adopted.axis.points,):
            raise ValueError(f"raw の点数 {raw.shape} が軸の点数 {adopted.axis.points} と違う")
        mode = Mode(adopted.mode)
        if mode == Mode.NF:
            raise NotImplementedError("NF は HOT と COLD の組から level/nf.py が出す（設計M）")

        base = self.ad_to_db(raw)
        cal = self._cal
        if cal is None:
            # 校正データなし: すべて 0 で換算し、測定は止めない（設計D §5.5）
            return ConvertedLevels(
                dbm=base,
                out_of_cal_range=np.zeros(base.shape, dtype=bool),
                no_calibration=True,
            )

        dbm = base + cal.level_offset_db
        flag = np.zeros(base.shape, dtype=bool)
        missing = False
        if mode in (Mode.SA, Mode.SG):
            # RBW の補正は実際に使う RBW（採用値）で引く
            rbw_off = cal.rbw_offset_db.get(int(adopted.rbw_hz))
            if rbw_off is None:
                missing = True
                rbw_off = 0.0
            c, flag = self.corr(adopted.axis.freqs())
            dbm = dbm + rbw_off + c
        return ConvertedLevels(dbm=dbm, out_of_cal_range=flag, no_calibration=False, rbw_offset_missing=missing)


def dbm_to_watt(dbm: np.ndarray) -> np.ndarray:
    """dBm からリニア（W）へ: W = 10^((P − 30)/10)（設計D §5.3。トレースはリニアで持つ: 設計F）。"""
    return np.power(10.0, (np.asarray(dbm, dtype=np.float64) - 30.0) / 10.0)
