"""記録した生の値をレベルとフレームにする（再生: 設計P §5・記録中に読み直す古い行: P §3）。

記録には生の値と、そのとき使った条件・校正・機種のプロファイルが入っている（設計H §3）。ここではそれを使って、
測ったときと同じ換算で dB とフレーム（トレースが受け取る形: 設計F §5.1）を作る。いまつないでいる機器の校正は使わない
（記録した校正の写しを使う）。基準との比（ノーマライズ）を当てるのは呼ぶ側（基準のファイルを読むので）。

- GigaSt の AD 値: LevelConverter（設計D §5.3）。行をまとめて換算するときは、点ごとの足す量を一度だけ求める
  （AD 値 → dB は点ごとに足す量が決まった1次の式なので、最大値でまとめた行を換算しても最大値の換算と同じ）
- RTL-SDR のリニアの電力: rtlsdr/levels.py（利得は区間の raw_extra、レベルの補正は記録した個体の校正）

Qt を import しない。
"""
from __future__ import annotations

import copy
import dataclasses
from typing import Optional

import numpy as np

from .devices.rtlsdr import levels as rtl_levels
from .level.calibration import Calibration
from .level.convert import LevelConverter, dbm_to_watt
from .storage import adopted_from_dict
from .storage.h5file import FileHeader, SegmentInfo
from .types import FreqAxis, Frame, Mode, Quantity, RawSweep, ValueKind

GIGAST_ENCODING = "gigast_ad_u16"


class SegmentLevels:
    """区間1つの換算。作れない区間（NF・知らない形）は can_convert が False。"""

    def __init__(self, seg: SegmentInfo, header: FileHeader) -> None:
        self.seg = seg
        self.profile = copy.deepcopy(header.profile or {})
        a = adopted_from_dict(seg.conditions)
        self.adopted = dataclasses.replace(a, axis=FreqAxis(seg.start_hz, seg.step_hz, seg.points))
        cal = seg.calibration if seg.calibration is not None else header.calibration
        self.encoding = seg.raw_encoding
        self.unit = "dBm"
        self.can_convert = True
        self._offset: Optional[np.ndarray] = None
        self._conv: Optional[LevelConverter] = None
        self._rtl_gain: Optional[float] = None
        self._rtl_offset: Optional[float] = None
        self._gain_corr: Optional[np.ndarray] = None
        self.no_calibration = cal is None
        if Mode(self.adopted.mode) == Mode.NF:
            self.can_convert = False
        elif self.encoding == GIGAST_ENCODING:
            try:
                calib = Calibration.from_dict(cal) if isinstance(cal, dict) else None
            except (KeyError, ValueError, TypeError):
                calib = None
            prof = self.profile if (self.profile.get("raw") or {}).get("encoding") == GIGAST_ENCODING else None
            if prof is None:
                self.can_convert = False
                return
            self._conv = LevelConverter(prof, calib)
            zero = np.zeros(seg.points, dtype=np.uint16)
            lv = self._conv.to_dbm(self.adopted, zero)
            self._offset = lv.dbm - self._conv.ad_to_db(zero)
            self._out_of_cal = np.asarray(lv.out_of_cal_range, dtype=bool)
            self.no_calibration = lv.no_calibration
        elif self.encoding == rtl_levels.ENCODING:
            extra = seg.raw_extra or {}
            gain = extra.get("gain_db")
            self._rtl_gain = None if gain is None else float(gain)
            # PlutoSDR の利得の補正（設計Q §7.6）。測ったときに当てた点ごとの値を足す（無ければ 0）
            corr = extra.get("gain_corr_db")
            self._gain_corr = None if corr is None else np.asarray(corr, dtype=float)
            offsets = None
            if isinstance(cal, dict):
                try:
                    from .instrument_rtlsdr import RtlSdrCalibration

                    offsets = RtlSdrCalibration.from_dict(cal).level_offset_db
                except (KeyError, ValueError, TypeError):
                    offsets = None
            if self._rtl_gain is not None:
                self._rtl_offset = rtl_levels.offset_for_gain(offsets, int(round(self._rtl_gain * 10)))
            self.unit = rtl_levels.to_level(np.zeros(1), self._rtl_gain, self._rtl_offset)[1]
            self.no_calibration = self.unit != rtl_levels.UNIT_DBM
        else:
            self.can_convert = False

    def db_rows(self, raw_rows: np.ndarray) -> np.ndarray:
        """採用した生の値の行 [行, 点] を dB [行, 点]（float32。値の無い点は NaN）にする。"""
        raw = np.asarray(raw_rows)
        if not self.can_convert:
            return np.full(raw.shape, np.nan, dtype=np.float32)
        if self._conv is not None and self._offset is not None:
            return (self._conv.ad_to_db(raw) + self._offset).astype(np.float32)
        lv, _unit = rtl_levels.to_level(raw, self._rtl_gain, self._rtl_offset)
        return np.asarray(self._with_gain_corr(lv), dtype=np.float32)

    def _with_gain_corr(self, level: np.ndarray) -> np.ndarray:
        # 補正は測ったときの全点（Stop の先の1点を含むことがある）で持つ。保存は Stop までの点なので頭から合わせる
        n = np.shape(level)[-1]
        if self._gain_corr is None or self._gain_corr.size < n:
            return level
        return level + self._gain_corr[:n]

    def frame(self, raw: RawSweep, sweep_no: int, display: Optional[tuple[int, int]] = None) -> Frame:
        """掃引1回分のフレーム（測ったときの instrument._build_frame と同じ形）。display は表示の範囲（Hz）。"""
        a = raw.adopted
        n = a.axis.points
        lo, hi = display if display is not None else (a.start_hz, a.stop_hz)
        prof = copy.deepcopy(self.profile)
        if self._conv is not None:
            levels = self._conv.to_dbm(a, raw.raw)
            transient = np.zeros(n, dtype=bool)
            transient[: max(0, min(n, int(raw.transient_head_points)))] = True
            values = dbm_to_watt(levels.dbm)
            flags = {"transient": transient, "out_of_cal_range": np.asarray(levels.out_of_cal_range, dtype=bool)}
        else:
            ex = raw.extra or {}
            level, unit = rtl_levels.to_level(raw.raw, self._rtl_gain, self._rtl_offset)
            level = self._with_gain_corr(level)
            no_value = np.zeros(n, dtype=bool)
            nv = (ex.get("flags") or {}).get("no_value")
            if nv is not None:
                nv = np.asarray(nv, dtype=bool)
                no_value[: min(n, nv.size)] = nv[:n]
            level = np.where(no_value, np.nan, level)
            prof["level_unit"] = unit
            prof["fft_window"] = ex.get("window")
            values = rtl_levels.to_linear(level)
            flags = {"transient": np.zeros(n, dtype=bool), "no_value": no_value}
        return Frame(axis=a.axis, values=values, quantity=Quantity.POWER, value_kind=ValueKind.REAL,
                     time=raw.started_at, duration_s=raw.duration_s, sweep_no=int(sweep_no),
                     display_start_hz=int(lo), display_stop_hz=int(hi), adopted=a, raw=raw, flags=flags,
                     profile=prof)
