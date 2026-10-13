"""PlutoSDR の基準の補正（設計Q §7.10）。

基準の水晶（公称 40MHz）がずれていると、送信・受信の LO とサンプリング周波数が同じ割合でずれる。ad9361-phy の属性
xo_correction に基準の本当の周波数（Hz）を書くと両方が合う。補正は ppm で持ち、書く値は round(公称 ×（1 + ppm × 1e-6）)。
書ける範囲は機器が xo_correction_available（"[最小 刻み 最大]"）で申告する（実機 [39992000 1 40008000]: ±200ppm）。

- 0ppm は書かない（機器が持っている値のまま）。機器の環境変数で基準を合わせてある PlutoSDR は、電源を入れたときに
  その値で動くので、補正を使わない利用者の機器の値を消さない。接続した後に 0 へ戻したときは、接続したときに読んだ値を
  書き戻す
- 範囲の外は書かずに断る（XoOutOfRange。機器の値はそのまま）
- 属性が読めない機器（古いファームウェアなど）は何もしない（supported が False）
- 書いた後はサンプリング周波数と LO を書き直す（呼ぶ側の機器の口: driver.set_xo_ppm）
"""
from __future__ import annotations

import math
from typing import Optional

from .iio import IioError

NOMINAL_HZ = 40_000_000
PPM_DECIMALS = 3
ATTR = "xo_correction"
ATTR_AVAILABLE = "xo_correction_available"
# 属性が無い（読めない）ときの errno。ほかの失敗は繋がりの失敗として呼ぶ側へ投げる
_MISSING = (2, 22, 38)  # ENOENT・EINVAL・ENOSYS


class XoOutOfRange(Exception):
    """書こうとした値が機器の申告の範囲の外（書いていない）。"""

    def __init__(self, ppm: float, hz: int, lo_hz: int, hi_hz: int) -> None:
        super().__init__(f"基準の補正 {ppm:g}ppm（{hz}Hz）は機器の範囲 {lo_hz}〜{hi_hz}Hz の外")
        self.ppm, self.hz, self.lo_hz, self.hi_hz = ppm, hz, lo_hz, hi_hz


def hz_for(ppm: float, nominal_hz: int = NOMINAL_HZ) -> int:
    """ppm の補正で書く基準の周波数（Hz の整数）。"""
    return int(round(nominal_hz * (1.0 + float(ppm) * 1e-6)))


def ppm_limits(lo_hz: int, hi_hz: int, nominal_hz: int = NOMINAL_HZ) -> tuple[float, float]:
    """書ける範囲（Hz）を ppm にする。小数 PPM_DECIMALS 桁へ内側に丸める（端の ppm で書く値が範囲に入る）。"""
    k = 10 ** PPM_DECIMALS

    def ppm(hz: int) -> float:
        return round((float(hz) / nominal_hz - 1.0) * 1e6 * k, 6)

    return math.ceil(ppm(lo_hz)) / k, math.floor(ppm(hi_hz)) / k


def round_ppm(ppm: float) -> float:
    return round(float(ppm), PPM_DECIMALS)


def _parse_hz(text: str) -> int:
    return int(round(float(str(text).split()[0])))


class XoState:
    """1台の機器の基準の補正の状態（機器の口が持ち、機器の口の錠の中だけで変える）。

    supported: 属性が読めたか（開いていなければ None）。hz: 機器から読み戻したいまの値。range_hz: 申告の範囲。
    boot_hz: 接続したときに読んだ値（0ppm へ戻したときに書き戻す）。ppm: このアプリが書いた補正（書いていなければ 0）。
    refused_ppm: 接続したときに範囲の外で書かなかった補正（無ければ None）。
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.supported: Optional[bool] = None
        self.hz: Optional[int] = None
        self.range_hz: Optional[tuple[int, int]] = None
        self.boot_hz: Optional[int] = None
        self.ppm = 0.0
        self.refused_ppm: Optional[float] = None

    def read(self, phy, parse_range) -> None:
        """申告の範囲といまの値を読む。属性が無ければ supported を False にする（ほかの失敗は IioError のまま投げる）。"""
        self.reset()
        try:
            r = parse_range(phy.attr(ATTR_AVAILABLE))
            hz = _parse_hz(phy.attr(ATTR))
        except IioError as e:
            if abs(int(e.code or 0)) in _MISSING:
                self.supported = False
                return
            raise
        except (ValueError, IndexError):
            self.supported = False  # 読めた文字が数でない（この属性を持たない機器とみなす）
            return
        self.supported = True
        self.hz = self.boot_hz = hz
        if r:
            self.range_hz = (int(r[0]), int(r[2]))

    def write(self, phy, ppm: float, nominal_hz: int = NOMINAL_HZ) -> bool:
        """補正 ppm を書いて読み戻す。書いたら True（属性が無い・0ppm で書き戻す値が無いときは False）。
        範囲の外は書かずに XoOutOfRange。"""
        if not self.supported:
            return False
        ppm = round_ppm(ppm)
        if ppm == 0.0:
            if self.boot_hz is None:
                return False
            hz = self.boot_hz
        else:
            hz = hz_for(ppm, nominal_hz)
        if self.range_hz is not None and not self.range_hz[0] <= hz <= self.range_hz[1]:
            raise XoOutOfRange(ppm, hz, self.range_hz[0], self.range_hz[1])
        phy.set_attr(ATTR, hz)
        self.ppm = ppm
        try:
            self.hz = _parse_hz(phy.attr(ATTR))
        except (ValueError, IndexError):
            self.hz = hz
        return True
