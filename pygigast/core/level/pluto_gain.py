"""PlutoSDR の受信の利得の補正の表（設計Q §7.6）。スレッド・機器を持たない計算と、表の形だけ。

解く問題: 受信の利得を変えると、同じ信号の表示のレベル（dBFS − 利得）が最大 約 5dB 動く（実測 2026-10-11）。
校正の作業で、周波数ごとに利得を帯の範囲いっぱいに 1dB 刻みで変えて自分の送信のトーンを測り、基準の利得
（30dB。その帯で使えなければ帯の中央）のときの値との差を補正の表にする。表示のレベルは

    dBFS − 利得 + 補正(周波数, 利得)

補正は足すもの（設計D §5.2・RTL-SDR のレベルの補正と同じ向き）なので、補正 = 基準の利得の読み − その利得の読み
（読みが基準より高く出る利得では負の補正で基準にそろえる。基準の利得の補正は 0）。周波数の間は線形補間、
表の周波数の範囲の外は端の値。利得が列の範囲の外（その周波数の帯で使えない利得）なら、その列の端の値。
絶対のレベル（dBm）はこの補正では決まらない（単位は「dB（未校正）」のまま）。
"""
from __future__ import annotations

import dataclasses
from typing import Optional

import numpy as np

from ..instrument_rtlsdr import RtlSdrCalibrationStore

FORMAT = "pygigast-plutosdr-gain-correction"
VERSION = 1
SUBDIR = "plutosdr"
CURRENT_FORMAT = "pygigast-plutosdr-gain-correction-current"
SOURCE_KIND = "calibration_work"

# 測る周波数: 70MHz と、100MHz〜6000MHz の 50MHz 刻み（120 点。設計Q §7.6 の「70MHz〜6GHz を 50MHz 刻み」）
GRID_LOW_HZ = 70_000_000
GRID_FIRST_STEP_HZ = 100_000_000
GRID_STEP_HZ = 50_000_000
GRID_TOP_HZ = 6_000_000_000
# 基準の利得（dB）。その帯で使えなければ帯の中央（設計Q §7.6）
REFERENCE_GAIN_DB = 30
# 外付けの減衰器として受け付ける値（dB。設計は 30〜40dB を使う。打ち間違いだけを弾く広さ）
EXT_ATTEN_DEFAULT_DB = 40.0
EXT_ATTEN_RANGE_DB = (20.0, 60.0)
# 確かめる画面で「前と大きく違う」とみなす差（dB）。実測の段差（約 2〜5dB）が戻ったら分かる大きさ
BIG_DIFF_DB = 1.0


def grid_freqs() -> list[int]:
    return [GRID_LOW_HZ] + list(range(GRID_FIRST_STEP_HZ, GRID_TOP_HZ + 1, GRID_STEP_HZ))


def reference_gain(lo_db: int, hi_db: int) -> int:
    """基準の利得: 30dB。その帯（lo〜hi）で使えなければ帯の中央（設計Q §7.6）。"""
    if lo_db <= REFERENCE_GAIN_DB <= hi_db:
        return REFERENCE_GAIN_DB
    return int(round((lo_db + hi_db) / 2))


def _r(x: float) -> float:
    return round(float(x), 2) + 0.0


@dataclasses.dataclass(frozen=True)
class GainRow:
    """1つの周波数の列: 測れた利得（小さい順）と、その利得の補正（dB）。"""

    freq_hz: int
    ref_gain_db: int
    gains_db: tuple
    corr_db: tuple
    tx_atten_db: tuple = ()  # 使った送信の減衰（区間ごと。記録）

    def at(self, gain_db: float) -> float:
        """その利得の補正。測れた利得の間は線形補間、列の外は端の値。"""
        return float(np.interp(float(gain_db), self.gains_db, self.corr_db))

    def extreme(self) -> float:
        """絶対値がいちばん大きい補正（確かめる画面の線と表に出す1つの値）。"""
        c = np.asarray(self.corr_db, dtype=np.float64)
        return float(c[int(np.argmax(np.abs(c)))]) if c.size else 0.0

    def to_dict(self) -> dict:
        return {"freq_hz": int(self.freq_hz), "ref_gain_db": int(self.ref_gain_db),
                "gains_db": [int(g) for g in self.gains_db], "corr_db": [_r(v) for v in self.corr_db],
                "tx_atten_db": [float(a) for a in self.tx_atten_db]}

    @classmethod
    def from_dict(cls, d: dict) -> "GainRow":
        gains = [int(g) for g in d["gains_db"]]
        corr = [float(v) for v in d["corr_db"]]
        if not gains or len(gains) != len(corr) or gains != sorted(set(gains)):
            raise ValueError("利得の列が不正")
        return cls(freq_hz=int(d["freq_hz"]), ref_gain_db=int(d["ref_gain_db"]), gains_db=tuple(gains),
                   corr_db=tuple(corr), tx_atten_db=tuple(float(a) for a in d.get("tx_atten_db") or ()))


def row_from_levels(freq_hz: int, levels: dict, lo_db: int, hi_db: int,
                    tx_atten_db: tuple = ()) -> Optional[GainRow]:
    """利得 → 読み（dBFS − 利得。同じ送信のレベルにそろえたもの）から列を作る。基準の利得が測れていなければ、測れた
    利得のうち基準にいちばん近いものを基準にする。測れた利得が無ければ None。"""
    if not levels:
        return None
    ref = reference_gain(lo_db, hi_db)
    gains = sorted(int(g) for g in levels)
    if ref not in levels:
        ref = min(gains, key=lambda g: (abs(g - ref), g))
    base = float(levels[ref])
    return GainRow(freq_hz=int(freq_hz), ref_gain_db=int(ref), gains_db=tuple(gains),
                   corr_db=tuple(_r(base - float(levels[g])) for g in gains), tx_atten_db=tuple(tx_atten_db))


@dataclasses.dataclass(frozen=True)
class GainCorrection:
    """PlutoSDR の個体（シリアル番号）の利得の補正の表。rows は周波数の小さい順。"""

    model: str
    serial: str
    created_at: str
    source: dict
    rows: tuple
    work: Optional[dict] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "_cache", {})

    @property
    def freqs_hz(self) -> list[int]:
        return [r.freq_hz for r in self.rows]

    def column(self, gain_db: float) -> np.ndarray:
        """その利得の、表の周波数ごとの補正。"""
        key = float(gain_db)
        c = self._cache.get(key)
        if c is None:
            c = np.array([r.at(key) for r in self.rows], dtype=np.float64)
            self._cache[key] = c
        return c

    def values_at(self, freqs_hz, gain_db: float) -> np.ndarray:
        """周波数の配列の補正（dB）。周波数の間は線形補間、表の範囲の外は端の値（np.interp と同じ）。"""
        f = np.asarray(freqs_hz, dtype=np.float64)
        if not self.rows:
            return np.zeros(f.shape)
        return np.interp(f, np.asarray(self.freqs_hz, dtype=np.float64), self.column(gain_db))

    def to_dict(self) -> dict:
        return {"format": FORMAT, "version": VERSION, "device": {"model": self.model, "serial": self.serial},
                "created_at": self.created_at, "source": dict(self.source),
                "rows": [r.to_dict() for r in self.rows], "work": self.work}

    @classmethod
    def from_dict(cls, d: dict) -> "GainCorrection":
        if d.get("format") != FORMAT:
            raise ValueError(f"format が {FORMAT} でない")
        if d.get("version") != VERSION:
            raise ValueError(f"version {d.get('version')!r} は読めない")
        rows = tuple(sorted((GainRow.from_dict(r) for r in d.get("rows") or []), key=lambda r: r.freq_hz))
        if len({r.freq_hz for r in rows}) != len(rows):
            raise ValueError("同じ周波数の列が2つある")
        return cls(model=str(d["device"]["model"]), serial=str(d["device"]["serial"]),
                   created_at=str(d["created_at"]), source=dict(d.get("source") or {}), rows=rows,
                   work=d.get("work") if isinstance(d.get("work"), dict) else None)


def merged_rows(base: Optional[GainCorrection], new_rows) -> tuple:
    """前の表に、測った列を差し替えた列の並び（途中でやめた作業を保存するとき、測っていない周波数は前の値のまま）。"""
    out = {r.freq_hz: r for r in (base.rows if base is not None else ())}
    for r in new_rows:
        out[r.freq_hz] = r
    return tuple(out[f] for f in sorted(out))


def extreme_at(cal: Optional[GainCorrection], freq_hz: int) -> float:
    """表のその周波数の「絶対値がいちばん大きい補正」（列の間は線形補間。表が無ければ 0 = 補正なし）。"""
    if cal is None or not cal.rows:
        return 0.0
    xs = np.asarray(cal.freqs_hz, dtype=np.float64)
    ys = np.asarray([r.extreme() for r in cal.rows], dtype=np.float64)
    return float(np.interp(float(freq_hz), xs, ys))


class GainCorrectionStore(RtlSdrCalibrationStore):
    """<root>/<シリアル番号>/plutosdr/<日時>.json に足していき、current.json が使うものを指す（RTL-SDR の個体の校正と
    同じ置き方: 設計Q §6.6。GigaSt の校正の履歴に混ぜない）。"""

    SUBDIR = SUBDIR
    CURRENT_FORMAT = CURRENT_FORMAT

    @staticmethod
    def _from_dict(d: dict) -> GainCorrection:
        return GainCorrection.from_dict(d)
