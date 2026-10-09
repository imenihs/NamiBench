"""計測器の本体のうち、PlutoSDR をつなぐ部分（設計Q §7）。Instrument に混ぜる。

- 受信のスペクトル・継ぎ合わせ・レベル（dBFS − 利得 を「dB（未校正）」）・振り切れの知らせ・中心を動かせないとき
  （rtl_tcp の相手）の扱いは RTL-SDR と同じ部品（instrument_rtlsdr.py）を使う。生の値の形も同じ
- ここに持つのは PlutoSDR に固有のもの: 設定（libiio の DLL のフォルダ・URI・受信の利得と自動・送信の減衰）、
  「利得を合わせる」の口、rtl_tcp の出口へ流す IQ（4.096MS/s を相手の求める整数分の1の速さに落とす）
- 個体の校正（レベルの dBm 化）はまだ無い（設計Q §7.1: 後で入れる）。それまでレベルは「dB（未校正）」
- 送信（SG・TG）は機器の層が掃引の間だけ出す。止めた・SA に戻した・掃引を渡していない間は止める（on_idle）
"""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np

from .devices.base import Device
from .devices.plutosdr import driver as pl_driver
from .devices.plutosdr.iio import find_dll as find_iio_dll
from .devices.rtlsdr.dll import dll_bits
from .instrument_common import CommandError
from .rtltcp import TUNER_UNKNOWN
from .rtltcp.decimate import IqDecimator, factor_for
from .trace import ConditionChange
from .types import AdoptedSweep, Mode, RawSweep

_UNSET = object()
DEFAULT_URI = pl_driver.DEFAULT_URI
# 受け付ける利得の範囲（dB）。機器の帯ごとの範囲は機器が決め、使えない値は範囲に収めて使う
GAIN_MIN_DB = -20.0
GAIN_MAX_DB = 100.0
# rtl_tcp の相手がサンプリング速度を送ってくるまでの出力の速さ（rtl_tcp の既定）
RTLTCP_DEFAULT_RATE = 2_048_000


def _is_number(v) -> bool:
    import math

    return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)


class PlutoSdrInstrumentDevice(Device):
    """本体が使う PlutoSDR の機器の口。中身は devices/plutosdr の PlutoSdrDevice のまま。

    fixed_center_fn・current_fixed は RTL-SDR の包み（instrument_rtlsdr.RtlSdrInstrumentDevice）と同じ意味。
    """

    fft_sdr = True

    def __init__(self, inner: pl_driver.PlutoSdrDevice) -> None:
        self.inner = inner
        self.fixed_center_fn: Callable[[], Optional[int]] = lambda: None
        self.current_fixed: Optional[int] = None

    def __getattr__(self, name):
        if name == "inner":
            raise AttributeError(name)
        return getattr(self.inner, name)

    def profile(self) -> dict:
        return self.inner.profile()

    def identity(self):
        return self.inner.identity()

    def _fix(self, sweep: AdoptedSweep) -> None:
        from .instrument_rtlsdr import align_to_axis

        want = self.fixed_center_fn()
        fc = None
        if want is not None:
            f = self.inner.profile()["freq"]
            fc = align_to_axis(int(want), sweep, int(f["min_hz"]), int(f["max_hz"]))
        self.inner.fixed_center_hz = fc
        self.current_fixed = fc

    def measure(self, sweep: AdoptedSweep, cancel=None) -> RawSweep:
        self._fix(sweep)
        self.inner.progress_sink = self.progress_sink
        return self.inner.measure(sweep, cancel)

    def fit_gain(self, sweep: AdoptedSweep, cancel=None, progress=None) -> dict:
        self._fix(sweep)
        return self.inner.fit_gain(sweep, cancel, progress)

    def on_idle(self) -> None:
        self.inner.on_idle()

    def close(self) -> None:
        self.inner.iq_sink = None
        self.inner.close()


def open_plutosdr_device(lib, uri: str, profile: Optional[dict] = None) -> PlutoSdrInstrumentDevice:
    """URI で開く。利得・送信の減衰は開いた後に本体が入れ直す（_plutosdr_prepare）。"""
    dev = pl_driver.PlutoSdrDevice(profile, lib=lib)
    dev.open(uri)
    return PlutoSdrInstrumentDevice(dev)


class PlutoSdrMixin:
    """PlutoSDR の設定・状態・命令（Instrument に混ぜる）。"""

    def _init_plutosdr(self) -> None:
        cfg = self._settings.get("plutosdr") or {}
        folder = cfg.get("dll_folder")
        uri = cfg.get("uri")
        gain = cfg.get("gain_db")
        atten = cfg.get("tx_atten_db")
        self._pluto_cfg = {
            "dll_folder": folder if isinstance(folder, str) and folder else None,
            "uri": uri if isinstance(uri, str) and uri else DEFAULT_URI,
            "gain_db": float(gain) if _is_number(gain) else None,
            "agc": bool(cfg.get("agc", False)),
            "tx_atten_db": float(atten) if _is_number(atten) else None,
        }
        self._pluto_decim: Optional[IqDecimator] = None
        self._pluto_gain_sent: Optional[float] = None
        self._plutosdr_find_dll()
        self._plutosdr_push_backend()

    def _plutosdr_find_dll(self) -> None:
        path = find_iio_dll(self._pluto_cfg["dll_folder"])
        self._pluto_dll_path = None if path is None else str(path)
        self._pluto_dll_bits = dll_bits(path)

    def _plutosdr_push_backend(self) -> None:
        b = self._backends.get("plutosdr")
        if b is not None:
            if hasattr(b, "set_dll_folder"):
                b.set_dll_folder(self._pluto_cfg["dll_folder"])
            if hasattr(b, "set_uri"):
                b.set_uri(self._pluto_cfg["uri"])

    def _is_plutosdr(self) -> bool:
        return isinstance(self._device, PlutoSdrInstrumentDevice)

    def _plutosdr_device(self) -> PlutoSdrInstrumentDevice:
        if self._conn != "connected" or not self._is_plutosdr():
            raise CommandError("plutosdr_not_connected")
        return self._device

    def _plutosdr_profile(self) -> dict:
        if self._is_plutosdr():
            return self._profile
        return pl_driver.default_profile()

    def _plutosdr_gain_setting(self) -> Optional[float]:
        if self._pluto_cfg["agc"]:
            return None
        g = self._pluto_cfg["gain_db"]
        if g is None:
            g = float(self._plutosdr_profile()["gain"]["default_db"])
        return g

    def _plutosdr_atten_setting(self) -> float:
        a = self._pluto_cfg["tx_atten_db"]
        if a is None:
            a = float(self._plutosdr_profile()["source"]["tx_atten_db"]["default"])
        return a

    def _plutosdr_state(self) -> dict:
        dev = self._device if self._is_plutosdr() else None
        prof = self._plutosdr_profile()
        rng = prof["source"]["tx_atten_db"]
        limits = None
        if dev is not None:
            limits = list(dev.gain_limits_for(self._adopted))
        setting = self._plutosdr_gain_setting()
        applied = None if dev is None else dev.gain_db
        return {
            "dll_folder": self._pluto_cfg["dll_folder"],
            "dll_found": self._pluto_dll_path is not None,
            "dll_path": self._pluto_dll_path,
            "dll_bits": self._pluto_dll_bits,
            "uri": self._pluto_cfg["uri"],
            "gain_db": self._pluto_cfg["gain_db"],
            "gain_default_db": float(prof["gain"]["default_db"]),
            "agc": self._pluto_cfg["agc"],
            "tx_atten_db": self._plutosdr_atten_setting(),
            "tx_atten_range": {"min": float(rng["min"]), "max": float(rng["max"]), "step": float(rng["step"])},
            "connected": dev is not None,
            "gain_limits_db": limits,
            # いまの条件で使う利得（設定が範囲の外なら範囲に収めた値: 機器の帯で範囲が変わる）
            "gain_db_used": None if setting is None or limits is None else float(min(max(setting, limits[0]), limits[1])),
            "gain_db_actual": applied,
            "level_unit": self._rtlsdr_unit if dev is not None else None,
            "overload": self._rtlsdr_overload_last if dev is not None else False,
            "fitting": any(t.kind == "plutosdr_fit_gain" for t in getattr(self, "_tasks", {}).values()),
            "tx_on": None if dev is None else dev.tx_on,
            "fw_version": None if dev is None else dev.fw_version,
        }

    def _plutosdr_save_settings(self) -> None:
        self._settings.set("plutosdr", dict(self._pluto_cfg))
        try:
            self._settings.save()
        except OSError as e:
            self._notice("settings_unwritable", {"message": str(e)})

    # ------------------------------------------------------------------ 接続

    def _plutosdr_prepare(self, dev: PlutoSdrInstrumentDevice) -> None:
        """機器スレッドを始める前: 利得・送信の減衰を入れ、rtl_tcp へ IQ を流す口をつなぐ。"""
        self._rtlsdr_cal = None  # PlutoSDR の個体の校正はまだ無い（設計Q §7.1）
        try:
            self._pluto_gain_sent = self._plutosdr_gain_setting()
            dev.set_gain(self._pluto_gain_sent)
            dev.set_tx_atten(self._plutosdr_atten_setting())
        except Exception as e:  # noqa: BLE001 — 入れられなくても測れるので知らせて続ける
            self._notice("plutosdr_setting_failed", {"message": str(e)})
        dev.fixed_center_fn = self._rtlsdr_fixed_center
        dev.inner.iq_sink = self._plutosdr_make_sink(dev)
        fs = int(dev.profile()["fft"]["sample_rate_hz"])
        want = self._rtl_requested.get("set_sample_rate", RTLTCP_DEFAULT_RATE)
        self._pluto_decim = IqDecimator(fs, want if factor_for(fs, int(want or 0)) else RTLTCP_DEFAULT_RATE)
        self._rtlsdr_unit = None
        self._rtlsdr_overload_key = None
        self._rtlsdr_overload_last = False

    # ------------------------------------------------------------------ 命令

    def _cmd_plutosdr_set(self, dll_folder=_UNSET, uri=_UNSET, gain_db=_UNSET, agc=_UNSET,
                          tx_atten_db=_UNSET) -> dict:
        """libiio の DLL のフォルダ・URI・受信の利得（手動の dB か自動）・送信の減衰を変え、設定に残す（設計Q §7）。"""
        if dll_folder is not _UNSET and dll_folder is not None and not isinstance(dll_folder, str):
            raise CommandError("bad_value", "dll_folder")
        if uri is not _UNSET:
            if not isinstance(uri, str) or not uri.strip() or ":" not in uri:
                raise CommandError("bad_value", "uri")
            uri = uri.strip()
        if gain_db is not _UNSET and gain_db is not None:
            if not _is_number(gain_db) or not GAIN_MIN_DB <= gain_db <= GAIN_MAX_DB:
                raise CommandError("bad_value", "gain_db", {"min": GAIN_MIN_DB, "max": GAIN_MAX_DB})
        if agc is not _UNSET and not isinstance(agc, bool):
            raise CommandError("bad_value", "agc")
        if agc is True and gain_db is not _UNSET and gain_db is not None:
            raise CommandError("bad_value", "agc", {"conflict": "gain_db"})
        if tx_atten_db is not _UNSET:
            rng = self._plutosdr_profile()["source"]["tx_atten_db"]
            lo, hi = float(rng["min"]), float(rng["max"])
            if not _is_number(tx_atten_db) or not lo <= tx_atten_db <= hi:
                raise CommandError("bad_value", "tx_atten_db", {"min": lo, "max": hi})
        if dll_folder is not _UNSET:
            self._pluto_cfg["dll_folder"] = dll_folder or None
            self._plutosdr_find_dll()
        if uri is not _UNSET:
            self._pluto_cfg["uri"] = uri
        if dll_folder is not _UNSET or uri is not _UNSET:
            self._plutosdr_push_backend()
            self._devices_changed()
        if gain_db is not _UNSET or agc is not _UNSET:
            if gain_db is not _UNSET and gain_db is not None:
                self._pluto_cfg["gain_db"] = float(gain_db)
                self._pluto_cfg["agc"] = False
            if agc is not _UNSET:
                self._pluto_cfg["agc"] = agc
            self._plutosdr_apply_gain()
        if tx_atten_db is not _UNSET:
            step = float(self._plutosdr_profile()["source"]["tx_atten_db"]["step"] or 0.25)
            v = round(round(float(tx_atten_db) / step) * step, 2)
            changed = v != self._plutosdr_atten_setting()
            self._pluto_cfg["tx_atten_db"] = v
            if self._is_plutosdr():
                self._device.set_tx_atten(v)
                if changed and self._adopted.mode in (Mode.SG, Mode.TG):
                    # 出力のレベルが変わると受ける値の意味が変わる（設計F §5.3 と同じ扱い）
                    self._rtlsdr_changed_meaning("tx_atten")
        self._plutosdr_save_settings()
        self._changed()
        return {"plutosdr": self._plutosdr_state()}

    def _plutosdr_apply_gain(self) -> None:
        if not self._is_plutosdr():
            return
        want = self._plutosdr_gain_setting()
        before = self._pluto_gain_sent
        self._device.set_gain(want)
        self._pluto_gain_sent = want
        if want != before:
            self._rtlsdr_changed_meaning("gain")

    def _cmd_plutosdr_fit_gain(self) -> dict:
        """「利得を合わせる」（設計Q §6.5 と同じ）。自動の間は受け付けない。"""
        dev = self._plutosdr_device()
        if self._pluto_cfg["agc"]:
            raise CommandError("rtlsdr_fit_needs_manual_gain")

        def done(res: dict) -> None:
            self._pluto_cfg["gain_db"] = float(res["gain_db"])
            self._pluto_cfg["agc"] = False
            self._plutosdr_save_settings()
            dev.set_gain(float(res["gain_db"]))
            self._pluto_gain_sent = float(res["gain_db"])

        lo, hi = dev.gain_limits_for(self._adopted)
        total = max(1, int(np.ceil(np.log2(hi - lo + 2))) + 1)
        return self._sdr_start_fit_gain("plutosdr_fit_gain", dev, done, total)

    # ------------------------------------------------------------------ rtl_tcp

    def _plutosdr_rtltcp_header(self) -> tuple[int, int]:
        """チューナーの種類は 0（不明: R820T などではない）、利得の段数は受信の利得の段の数。"""
        lo, hi = self._device.gain_limits_for(self._adopted)
        return TUNER_UNKNOWN, int(hi - lo + 1)

    def _plutosdr_tcp_command(self, name: str, value) -> None:
        """相手のサンプリング速度・利得の命令（できるものは従う: 設計Q §6.7）。"""
        if name == "set_sample_rate":
            d = self._pluto_decim
            if d is not None and isinstance(value, int) and d.set_out_rate(value):
                return
            fs = int(self._profile["fft"]["sample_rate_hz"])
            self._notice("rtltcp_sample_rate_unsupported", {"requested": value, "used": None if d is None else d.fs_out,
                                                            "read_rate": fs})
            return
        want = _UNSET
        if name == "set_gain_mode":
            if value == 0:
                want = None
            elif self._pluto_cfg["agc"]:
                want = self._pluto_cfg["gain_db"] if self._pluto_cfg["gain_db"] is not None else \
                    float(self._profile["gain"]["default_db"])
        elif name == "set_gain" and isinstance(value, int):
            want = value / 10.0
        elif name == "set_gain_by_index" and isinstance(value, int):
            lo, hi = self._device.gain_limits_for(self._adopted)
            if 0 <= value <= hi - lo:
                want = float(lo + value)
        if want is _UNSET:
            return
        before = (self._pluto_cfg["gain_db"], self._pluto_cfg["agc"])
        if want is None:
            self._pluto_cfg["agc"] = True
        else:
            self._pluto_cfg["gain_db"] = float(min(max(want, GAIN_MIN_DB), GAIN_MAX_DB))
            self._pluto_cfg["agc"] = False
        if (self._pluto_cfg["gain_db"], self._pluto_cfg["agc"]) == before:
            return
        self._plutosdr_apply_gain()
        self._plutosdr_save_settings()
        self._notice("rtltcp_gain_from_client", {"gain_db": self._pluto_cfg["gain_db"], "agc": self._pluto_cfg["agc"]})
        self._changed()

    def _plutosdr_make_sink(self, dev: PlutoSdrInstrumentDevice) -> Callable[[np.ndarray, int, int], None]:
        def sink(iq: np.ndarray, center: int, fs: int) -> None:
            fc = dev.current_fixed
            if fc is None or center != fc:
                return  # 中心を動かす測定（継ぎ合わせ）の IQ は外へ出さない
            s = self._rtl_server
            d = self._pluto_decim
            if s is None or not s.running or s.client_count == 0 or d is None:
                return
            self.push_device_iq(d.process(iq), d.fs_out)

        return sink
