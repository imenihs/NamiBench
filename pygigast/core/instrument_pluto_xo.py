"""計測器の本体のうち、PlutoSDR の基準の補正（設計Q §7.10）。Instrument に混ぜる。

基準の水晶（公称 40MHz）のずれを ppm で直す。ずれていると送信・受信の LO とサンプリング周波数が同じ割合でずれる。
- 補正は機器（シリアル番号）ごとに設定（settings.json の plutosdr.xo_ppm）に覚える。既定 0（書かない: 機器の値のまま）
- 繋ぐときは機器の口が開く途中で書く（送信を止めた後、LO の書ける下限を確かめる前: driver.open）。本体は値の表の写しを
  機器の入口（backend の set_xo_ppm）へ渡しておく。範囲の外で書けなかったときは知らせる（接続は続ける）
- 繋いでいる PlutoSDR の値を変えるのは、掃引も送信もしていないときだけ（掃引の途中で基準が変わると、1回の掃引の中で
  周波数の意味が変わる。外部 IQ の送信は出している IQ の周波数が変わる）。書いたら機器の口がサンプリング周波数を書き
  直し、LO は次の測定で書き直す。周波数の意味が変わるのでトレースの蓄積を捨てる（RTL-SDR の ppm と同じ: 設計Q §6.6）
- 繋いでいないとき（ほかの機器に繋いでいるときも）は値を覚えるだけ（次にその機器へ繋いだときに書く）。どの機器の値かは
  繋いでいる PlutoSDR、無ければ最後に繋いだ PlutoSDR（plutosdr.xo_serial）
"""
from __future__ import annotations

import math
from typing import Optional

from .devices.plutosdr import driver as pl_driver
from .devices.plutosdr import xo
from .instrument_common import CommandError
from .instrument_pluto_ref_fit import FIT_TASK_KINDS
from .types import DeviceDisconnected, DeviceError, InvalidSweep

_DEFAULT_XO: Optional[dict] = None


def _is_number(v) -> bool:
    return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)


def load_xo_settings(cfg: dict) -> dict:
    """設定の plutosdr から基準の補正の項目を読む（読めない値は捨てる。範囲は繋いだ機器の申告で確かめる）。"""
    raw = cfg.get("xo_ppm")
    table = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if isinstance(k, str) and k and _is_number(v) and xo.round_ppm(v) != 0.0:
                table[k] = xo.round_ppm(v)
    last = cfg.get("xo_serial")
    return {"xo_ppm": table, "xo_serial": last if isinstance(last, str) and last else None}


def _default_xo() -> dict:
    global _DEFAULT_XO
    if _DEFAULT_XO is None:
        _DEFAULT_XO = dict(pl_driver.default_profile().get("xo") or {})
    return _DEFAULT_XO


def xo_limits(prof_xo: dict) -> tuple[float, float]:
    """プロファイルの xo（繋いだ機器なら申告の範囲）から、受け付ける ppm の範囲。"""
    nominal = int(prof_xo.get("nominal_hz", xo.NOMINAL_HZ))
    return xo.ppm_limits(int(prof_xo.get("min_hz", nominal)), int(prof_xo.get("max_hz", nominal)), nominal)


class PlutoXoMixin:
    """PlutoSDR の基準の補正の状態・命令（Instrument に混ぜる）。"""

    def _pluto_xo_target(self) -> Optional[str]:
        """補正の欄が指す機器のシリアル番号（繋いでいる PlutoSDR、無ければ最後に繋いだ PlutoSDR）。"""
        if self._conn == "connected" and self._is_plutosdr():
            return (self._identity or {}).get("serial") or None
        return self._pluto_cfg.get("xo_serial")

    def _pluto_xo_live(self, serial: Optional[str]):
        """serial の PlutoSDR に繋いでいればその機器の口（書く先）。繋いでいなければ None（覚えるだけ）。"""
        if serial and self._conn == "connected" and self._is_plutosdr() \
                and (self._identity or {}).get("serial") == serial:
            return self._device
        return None

    def _pluto_xo_prof(self, live: bool) -> dict:
        return (self._profile.get("xo") or _default_xo()) if live else _default_xo()

    def _pluto_xo_lock(self) -> Optional[str]:
        """繋いでいる PlutoSDR の補正をいま変えられない理由のコード（掃引も送信もしていなければ None）。"""
        if self._exttx_active():
            return "ext_tx_active"
        if getattr(self, "_cal_job", None) is not None:
            return "calibration_in_progress"
        if any(t.kind in FIT_TASK_KINDS for t in self._tasks.values()):
            return "rtlsdr_fit_running"
        if self._run != "stopped" or self._ref_job is not None:
            return "plutosdr_xo_busy"
        return None

    def _pluto_xo_state(self) -> dict:
        serial = self._pluto_xo_target()
        dev = self._pluto_xo_live(serial)
        prof = self._pluto_xo_prof(dev is not None)
        lo, hi = xo_limits(prof)
        ds = dev.xo_state if dev is not None else {}
        return {
            "serial": serial,
            "ppm": float(self._pluto_cfg["xo_ppm"].get(serial, 0.0)) if serial else 0.0,
            "min_ppm": lo,
            "max_ppm": hi,
            "nominal_hz": int(prof.get("nominal_hz", xo.NOMINAL_HZ)),
            # 繋いでいるその機器へ書くか（False は覚えるだけ: 次にその機器へ繋いだときに書く）
            "live": dev is not None,
            # 機器が属性を持つか・読み戻したいまの基準の Hz・申告の範囲（繋いでいなければ None）
            "supported": ds.get("supported"),
            "hz": ds.get("hz"),
            "range_hz": ds.get("range_hz"),
            # 繋いだときに範囲の外で書かなかった補正（無ければ None）
            "refused_ppm": ds.get("refused_ppm"),
            "lock": self._pluto_xo_lock() if dev is not None else None,
        }

    def _pluto_xo_attached(self, dev) -> None:
        """繋いだ（機器の口は開く途中で補正を書いた）: 最後に繋いだ PlutoSDR を覚え、範囲の外で書けなかったら知らせる。"""
        serial = dev.identity().serial
        if serial and serial != self._pluto_cfg.get("xo_serial"):
            self._pluto_cfg["xo_serial"] = serial
            self._plutosdr_save_settings()
        refused = dev.xo_state.get("refused_ppm")
        if refused is not None:
            lo, hi = xo_limits(dev.profile().get("xo") or _default_xo())
            self._notice("plutosdr_xo_refused", {"ppm": refused, "min": lo, "max": hi})

    def _cmd_plutosdr_xo_set(self, ppm, serial=None) -> dict:
        """基準の補正（ppm）を変えて設定に残す（設計Q §7.10）。serial を省くと補正の欄が指す機器。

        繋いでいるその PlutoSDR には、掃引も送信もしていないときだけ書く（測っている・送っている間は理由付きで断る）。
        繋いでいなければ値を覚えるだけ。"""
        if not _is_number(ppm):
            raise CommandError("bad_value", "ppm")
        if serial is not None and (not isinstance(serial, str) or not serial):
            raise CommandError("bad_value", "serial")
        target = serial or self._pluto_xo_target()
        if not target:
            raise CommandError("plutosdr_not_connected", "ppm")
        dev = self._pluto_xo_live(target)
        lo, hi = xo_limits(self._pluto_xo_prof(dev is not None))
        ppm = xo.round_ppm(ppm)
        if not lo <= ppm <= hi:
            raise CommandError("bad_value", "ppm", {"min": lo, "max": hi})
        before = float(self._pluto_cfg["xo_ppm"].get(target, 0.0))
        if dev is not None:
            why = self._pluto_xo_lock()
            if why is not None:
                raise CommandError(why, "ppm")
            if not dev.xo_state.get("supported"):
                raise CommandError("plutosdr_xo_unsupported", "ppm")
            try:
                dev.set_xo_ppm(ppm)
            except xo.XoOutOfRange as e:
                lo, hi = xo.ppm_limits(e.lo_hz, e.hi_hz, int(self._pluto_xo_prof(True).get("nominal_hz", xo.NOMINAL_HZ)))
                raise CommandError("bad_value", "ppm", {"min": lo, "max": hi})
            except InvalidSweep:
                raise CommandError("ext_tx_active", "ppm")
            except DeviceDisconnected:
                raise CommandError("disconnected", "ppm")
            except DeviceError as e:
                raise CommandError("device_error", "ppm", {"message": str(e)})
        if ppm == 0.0:
            self._pluto_cfg["xo_ppm"].pop(target, None)
        else:
            self._pluto_cfg["xo_ppm"][target] = ppm
        self._plutosdr_save_settings()
        self._plutosdr_push_backend()
        if dev is not None and ppm != before:
            self._rtlsdr_changed_meaning("xo")  # 周波数の意味が変わる（RTL-SDR の ppm と同じ扱い）
        self._changed()
        return {"plutosdr": self._plutosdr_state()}
