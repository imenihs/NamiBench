"""計測器の本体のうち、NF 測定（設計M）: ENR・HOT と COLD の組から NF の Frame・NF モードに入ったときの案内。

Instrument（instrument.py）に混ぜて使う。ここのメソッドはすべて制御スレッドから呼ばれる（設計A §4.2）。
- ENR は利用者の設定（dB の小数か、周波数ごとの表: 設計M §1。前回終了時の状態に入る）。表を読み込んでいる間は表を使う。
  どちらも入れていなければ、校正データの ENR（旧校正ファイルの ENR を引き継いだもの: 設計D §5.2・K §4.6）を使う。
  どれも無ければ NF を出せない（点は値なし。画面は ENR の欄で知らせる）
- ENR が変わると NF の値の意味が変わるので、NF モードの間はトレースの蓄積を捨てる（設計F §5.3 と同じ扱い）
- 増幅度（旧 NF_Amp）は校正データの nf.amp_raw（設計K §4.4）。無ければ倍率 1
- HOT・COLD の電力は補正を当てない値（設計D §3: NF では周波数特性・全体のレベルの補正を当てない。差だけを使う）
- 必要な外部機器とつなぎ方は、NF モードに初めて入ったときに1回だけ画面へ知らせる（知らせ nf_guide: 設計M・U-02）
- 命令は設計A §4.3 の形。失敗は CommandError（コード）。文言は画面側（U-22）
"""
from __future__ import annotations

import copy
import math
from typing import Optional

import numpy as np

from .instrument_common import CommandError
from .level import nf as nfc
from .level.convert import dbm_to_watt
from .trace import ConditionChange
from .types import Frame, Mode, Quantity, RawSweep, ValueKind


class NfMixin:
    """NF 測定（Instrument に混ぜる）。"""

    # ------------------------------------------------------------------ 初期化・状態の保存

    def _init_nf(self, st: Optional[dict]) -> None:
        d = (st or {}).get("nf") or {}
        v = d.get("enr_db")
        ok = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) \
            and nfc.ENR_RANGE_DB[0] <= v <= nfc.ENR_RANGE_DB[1]
        self._nf_enr_db: Optional[float] = float(v) if ok else None
        self._nf_table: Optional[nfc.EnrTable] = None
        if isinstance(d.get("table"), dict):
            try:
                self._nf_table = nfc.EnrTable.from_dict(d["table"])
            except (KeyError, TypeError, ValueError, IndexError):
                self._nf_table = None  # 読めない表は使わない（ENR の欄が知らせる）
        self._nf_guide_shown = bool(d.get("guide_shown"))

    def _nf_state_to_save(self) -> dict:
        return {"nf": {"enr_db": self._nf_enr_db,
                       "table": None if self._nf_table is None else self._nf_table.to_dict(),
                       "guide_shown": self._nf_guide_shown}}

    # ------------------------------------------------------------------ いま使う値

    def _nf_calibration_enr(self) -> Optional[float]:
        cal = getattr(self, "_cal", None)
        if cal is None or cal.nf is None or cal.nf.enr_db is None:
            return None
        return float(cal.nf.enr_db)

    def _nf_enr(self) -> Optional[nfc.Enr]:
        """NF に使う ENR: 表 → 入れた値 → 校正データの値。どれも無ければ None。"""
        if self._nf_table is not None:
            return nfc.Enr(nfc.ENR_TABLE, table=self._nf_table)
        if self._nf_enr_db is not None:
            return nfc.Enr(nfc.ENR_VALUE, db=self._nf_enr_db)
        c = self._nf_calibration_enr()
        return None if c is None else nfc.Enr(nfc.ENR_CALIBRATION, db=c)

    def _nf_amp_raw(self) -> Optional[float]:
        cal = getattr(self, "_cal", None)
        return None if cal is None or cal.nf is None else float(cal.nf.amp_raw)

    def _nf_record(self, mode=None) -> Optional[dict]:
        """保存・記録に書く NF の値（あとで NF を計算し直せるように: 設計H §3）。mode（掃引のモード。省くといまの
        モード）が NF でなければ None。"""
        if (self._adopted.mode if mode is None else mode) != Mode.NF:
            return None
        enr = self._nf_enr()
        return {"enr": None if enr is None else enr.record(), "amp_raw": self._nf_amp_raw(),
                "small_difference_db": nfc.SMALL_DIFFERENCE_DB}

    def _nf_state(self) -> dict:
        """状態の nf（画面の NF の欄: 設計J §4.5・M）。"""
        enr = self._nf_enr()
        t = self._nf_table
        a = self._adopted
        covers = None
        if t is not None:
            lo, hi = min(a.start_hz, a.stop_hz), max(a.start_hz, a.stop_hz)
            covers = t.start_hz <= lo and hi <= t.stop_hz
        return {
            # いま使う ENR（kind: value・table・calibration。無ければ None）
            "enr": None if enr is None else {"kind": enr.kind, "db": enr.db},
            "enr_db": self._nf_enr_db,
            "calibration_enr_db": self._nf_calibration_enr(),
            "table": None if t is None else nfc.table_summary(t),
            # 表がいまの Start〜Stop を覆うか（覆わない所は端の値で計算し、点に印: 設計M §1）
            "table_covers": covers,
            "amp_raw": self._nf_amp_raw(),
            "small_difference_db": nfc.SMALL_DIFFERENCE_DB,
            "guide_shown": self._nf_guide_shown,
        }

    # ------------------------------------------------------------------ 掃引1回

    def _build_nf_frame(self, raw: RawSweep) -> Frame:
        """HOT・COLD の組（設計B §7.2）→ NF の Frame（設計M §1）。HOT・COLD の電力はトレースの平均が使う。"""
        a = raw.adopted
        conv = self._converter
        hot_raw = raw.raw_hot if raw.raw_hot is not None else raw.raw
        cold_raw = raw.raw_cold if raw.raw_cold is not None else raw.raw
        hot = dbm_to_watt(conv.ad_to_db(hot_raw))
        cold = dbm_to_watt(conv.ad_to_db(cold_raw))
        enr = self._nf_enr()
        enr_db = outside = None
        if enr is not None:
            enr_db, outside = enr.at(a.axis.freqs())
            if enr.kind != nfc.ENR_TABLE:
                outside = None
        parts = nfc.NfParts(hot_w=hot, cold_w=cold, enr_db=enr_db, amp_raw=self._nf_amp_raw(), enr_outside=outside)
        values, flags = parts.evaluate(hot, cold)
        self._no_calibration = getattr(self, "_cal", None) is None
        n = a.axis.points
        transient = np.zeros(n, dtype=bool)
        transient[: max(0, min(n, int(raw.transient_head_points)))] = True
        return Frame(
            axis=a.axis,
            values=values,
            quantity=Quantity.NF,
            value_kind=ValueKind.REAL,
            time=raw.started_at,
            duration_s=raw.duration_s,
            sweep_no=self._sweep_count,
            display_start_hz=a.start_hz,
            display_stop_hz=a.stop_hz,
            adopted=a,
            raw=raw,
            flags={"transient": transient, **flags},
            profile=copy.deepcopy(self._profile),
            nf=parts,
        )

    # ------------------------------------------------------------------ モードに入ったとき

    def _nf_on_mode(self, old_mode, new_mode) -> None:
        """NF モードに初めて入ったとき、必要な外部機器とつなぎ方を画面に1回だけ知らせる（設計M・U-02）。"""
        if new_mode == Mode.NF and old_mode != Mode.NF and not self._nf_guide_shown:
            self._nf_guide_shown = True
            self._notice("nf_guide", {})

    def _nf_enr_changed(self, before) -> None:
        """いま使う ENR が変わったら、NF モードの間はトレースの蓄積を捨てる（NF の値の意味が変わる）。"""
        after = self._nf_enr()

        def key(e):
            if e is None:
                return None
            return ("table", repr(e.table.to_dict())) if e.table is not None else ("db", e.db)

        if key(before) != key(after) and self._adopted.mode == Mode.NF:
            self._on_condition(ConditionChange.ENR)
        self._changed()

    # ------------------------------------------------------------------ 命令

    def _cmd_nf_set_enr(self, enr_db=None) -> dict:
        """ENR（dB の小数）を入れる。None は入れた値を消す（校正データの値があればそれを使う）。表を読み込んでいる間は
        表を使うので入れられない（表を外してから）。"""
        if enr_db is not None:
            if isinstance(enr_db, bool) or not isinstance(enr_db, (int, float)) or not math.isfinite(enr_db):
                raise CommandError("bad_value", "enr_db", {"value": enr_db})
            lo, hi = nfc.ENR_RANGE_DB
            if not lo <= float(enr_db) <= hi:
                raise CommandError("bad_value", "enr_db", {"value": enr_db, "min": lo, "max": hi})
        if self._nf_table is not None:
            raise CommandError("enr_table_in_use", "enr_db")
        before = self._nf_enr()
        self._nf_enr_db = None if enr_db is None else round(float(enr_db), 3)
        self._nf_enr_changed(before)
        return {"nf": self._nf_state()}

    def _cmd_nf_load_enr_table(self, text: str, file_name: str = "") -> dict:
        """ENR の表（CSV の中身）を読み込み、これから使う（設計M §1）。Qt も Web もファイルの中身を渡す（Web の口は
        パスを受け取らない: 設計A §4.3.2）。"""
        if not isinstance(text, str):
            raise CommandError("bad_value", "text")
        try:
            table = nfc.parse_enr_csv(text, str(file_name or ""))
        except nfc.EnrTableError as e:
            raise CommandError("enr_table_unreadable", "text", {"line": e.line, "reason": e.reason}) from None
        before = self._nf_enr()
        self._nf_table = table
        self._nf_enr_changed(before)
        return {"nf": self._nf_state(), "table": nfc.table_summary(table)}

    def _cmd_nf_clear_enr_table(self) -> dict:
        """ENR の表を外す（入れた値・校正データの値に戻る）。"""
        if self._nf_table is None:
            raise CommandError("enr_no_table")
        before = self._nf_enr()
        self._nf_table = None
        self._nf_enr_changed(before)
        return {"nf": self._nf_state()}
