"""RTL-SDR の生の値からレベルへの換算（設計Q §6.4 のレベル・§6.6 の個体の校正）。

生の値（encoding "rtlsdr_power_w"）は、振り切れの正弦波を 1.0 とするリニアの電力（dBFS の目盛。
NaN は値の無い点）。GigaSt の LevelConverter に当たる換算は次のとおり:

    レベル[dB] = 10·log10(生の値) − 利得[dB] + 個体のレベルの補正[dB]（任意）

- 個体のレベルの補正（利得の段ごと）が無いときの単位は「dB（未校正）」。未校正のまま dBm と書かない
- 補正があるときだけ単位を dBm とする
- 利得が分からないとき（チューナーの自動利得）は利得を引けないので dBFS のまま出す
- トレースはリニアで持つ（設計F）。dB の値を W と同じ式（10^((dB − 30)/10)）でリニアにしてよい。
  単位の名前だけを持ち歩き、平均・マーカーの計算は GigaSt と同じにできる
"""
from __future__ import annotations

from typing import Optional

import numpy as np

ENCODING = "rtlsdr_power_w"

UNIT_DBM = "dBm"
UNIT_UNCALIBRATED = "dB（未校正）"
UNIT_DBFS = "dBFS"

# log10 の 0 を避ける下限（振り切れに対して −300dB。値の無い点は NaN のまま）
_FLOOR = 1e-30


def dbfs(raw: np.ndarray) -> np.ndarray:
    """生の値（リニア）を dBFS に。NaN は NaN のまま。"""
    p = np.asarray(raw, dtype=np.float64)
    with np.errstate(invalid="ignore"):
        return 10.0 * np.log10(np.maximum(p, _FLOOR))


def offset_for_gain(offsets_db: Optional[dict], gain_tenth_db: Optional[int]) -> Optional[float]:
    """個体の校正の「利得の段ごとのレベルの補正」から、いまの段の値を引く（無ければ None）。"""
    if not offsets_db or gain_tenth_db is None:
        return None
    v = offsets_db.get(int(gain_tenth_db), offsets_db.get(str(int(gain_tenth_db))))
    return None if v is None else float(v)


def to_level(raw: np.ndarray, gain_db: Optional[float],
             level_offset_db: Optional[float] = None) -> tuple[np.ndarray, str]:
    """(レベルの配列[dB], 単位の名前) を返す。値の無い点は NaN。"""
    v = dbfs(raw)
    if gain_db is None:
        return v, UNIT_DBFS
    v = v - float(gain_db)
    if level_offset_db is None:
        return v, UNIT_UNCALIBRATED
    return v + float(level_offset_db), UNIT_DBM


def to_linear(level_db: np.ndarray) -> np.ndarray:
    """トレースに渡すリニアの値（dBm を W にするのと同じ式）。NaN は NaN のまま。"""
    return np.power(10.0, (np.asarray(level_db, dtype=np.float64) - 30.0) / 10.0)
