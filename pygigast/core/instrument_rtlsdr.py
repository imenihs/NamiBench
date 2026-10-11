"""計測器の本体のうち、RTL-SDR をつなぐ部分（設計Q §6）。

Instrument（instrument.py）に混ぜて使う。`_` で始まるメソッドは、断りが無ければ制御スレッドから呼ばれる。

- レベル（設計Q §6.4・§6.6）: 生の値（encoding "rtlsdr_power_w"）を devices/rtlsdr/levels.py で
  「dBFS − 利得 ＋ 個体のレベルの補正（あれば）」にする。単位は補正があれば dBm、無ければ「dB（未校正）」、
  AGC の間は dBFS。単位はトレースが持ち歩く（Frame.profile["level_unit"]。測った機器の性質と同じく
  トレースごとに持つ: 設計Q §2.1）。値の無い点（中心を動かせないときの DC ±δ など）は NaN と印 no_value
- 個体の校正（設計Q §6.6）: ppm と、利得の段ごとのレベルの補正。GigaSt の校正（設計D §5.2）と同じく
  <cal_root>/<シリアル番号>/ の下に履歴として足していき、current.json が使うものを指す。GigaSt の校正の
  形とは中身が違うので、同じフォルダの下の rtlsdr/ に分けて置く（GigaSt の履歴に混ぜない）
- 中心を動かせないとき（rtl_tcp の相手がつながっている間・聞いている間: 設計Q §6.4・§6.7・§6.8）は、
  機器の口を包んだ RtlSdrInstrumentDevice が、測る直前に「動かさない中心」を出力の格子へ合わせて機器へ渡す
  （掃引条件と中心を同じ機器スレッドの中で決めるので、条件の変更と中心の変更が食い違わない）
- IQ の取り出し: 機器の層の外へ IQ を出す例外は rtl_tcp の出口と聞く機能だけ（設計Q §3）。DLL の口を
  包んだ IqTapLib が読み取った 8bit の IQ を見せ、中心を動かさない測定の間だけ rtl_tcp と復調へ渡す
  （機器の層の中身には手を入れない。読みの流れは機器の層の手順のまま）
"""
from __future__ import annotations

import concurrent.futures
import dataclasses
import datetime
import json
import math
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .devices.base import Device
from .devices.rtlsdr import driver as rtl_driver
from .devices.rtlsdr import levels as rtl_levels
from .devices.rtlsdr.dll import READ_BLOCK, dll_bits, find_dll
from .instrument_common import CommandError
from .storage import Cancelled
from .rtltcp import TUNER_R820T
from .trace import ConditionChange
from .types import AdoptedSweep, Frame, Quantity, RawSweep, SweepDiscarded, SweepRequest, ValueKind

RTLSDR_ENCODING = rtl_levels.ENCODING

# 引数を渡さなかった（null と区別する）
_UNSET = object()

# 中心を動かせない理由（状態・理由のコードに出す）
CAUSE_RTLTCP = "rtltcp_clients"
CAUSE_LISTEN = "listening"

# ppm として受け付ける範囲（手持ちは約 +59ppm。TCXO なしの水晶でも ±100ppm 程度が一般的なので、
# 桁の打ち間違いだけを弾く広さにした）
PPM_LIMIT = 1000

# 利得として受け付ける範囲（dB。R820T の段は 0〜49.6dB。段の中で最も近いものを使う）
GAIN_MIN_DB = 0.0
GAIN_MAX_DB = 100.0


# ---------------------------------------------------------------------------
# 個体の校正（設計Q §6.6）
# ---------------------------------------------------------------------------

CAL_FORMAT = "pygigast-rtlsdr-calibration"
CAL_VERSION = 1
CAL_SUBDIR = "rtlsdr"
_CURRENT_FILE = "current.json"
_CURRENT_FORMAT = "pygigast-rtlsdr-calibration-current"
# フォルダ名に使えるシリアル番号（設計D §5.2 の校正の置き場所と同じ規則）
_SERIAL_RE = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*")


@dataclasses.dataclass(frozen=True)
class RtlSdrCalibration:
    """RTL-SDR の個体の校正。ppm は DLL へ渡す周波数の補正、level_offset_db は利得の段（0.1dB 単位の整数）
    ごとに足すレベルの補正（dB）。補正は足すもの（設計D §5.2 と同じ向き）。"""

    model: str
    serial: str
    created_at: str
    source: dict
    ppm: int = 0
    level_offset_db: dict = dataclasses.field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "format": CAL_FORMAT,
            "version": CAL_VERSION,
            "device": {"model": self.model, "serial": self.serial},
            "created_at": self.created_at,
            "source": dict(self.source),
            "ppm": int(self.ppm),
            # JSON のキーは文字列になるので、利得の段（0.1dB 単位の整数）を文字列で書く
            "level_offset_db": {str(int(k)): float(v) for k, v in self.level_offset_db.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RtlSdrCalibration":
        if d.get("format") != CAL_FORMAT:
            raise ValueError(f"format が {CAL_FORMAT} でない")
        if d.get("version") != CAL_VERSION:
            raise ValueError(f"version {d.get('version')!r} は読めない")
        ppm = d.get("ppm", 0)
        if isinstance(ppm, bool) or not isinstance(ppm, int):
            raise ValueError("ppm は整数")
        return cls(
            model=str(d["device"]["model"]),
            serial=str(d["device"]["serial"]),
            created_at=str(d["created_at"]),
            source=dict(d.get("source") or {}),
            ppm=ppm,
            level_offset_db={int(k): float(v) for k, v in (d.get("level_offset_db") or {}).items()},
        )


class RtlSdrCalibrationStore:
    """<root>/<シリアル番号>/rtlsdr/<日時>.json に足していき、同じフォルダの current.json が使うものを指す。

    やり直すたびに新しいファイルを足し、古いものを消さない・上書きしない（履歴。設計D §5.2 と同じ扱い）。
    SDR の個体の校正（PlutoSDR の利得の補正: 設計Q §7.6）も同じ置き方なので、SUBDIR・CURRENT_FORMAT・
    _from_dict を変えた子のクラスで使う。保存するものは created_at・serial・to_dict() を持つこと。
    """

    SUBDIR = CAL_SUBDIR
    CURRENT_FORMAT = _CURRENT_FORMAT

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @staticmethod
    def _from_dict(d: dict):
        return RtlSdrCalibration.from_dict(d)

    def _dir(self, serial: str) -> Path:
        if not _SERIAL_RE.fullmatch(serial or ""):
            raise ValueError(f"フォルダ名に使えないシリアル番号: {serial!r}")
        return self.root / serial / self.SUBDIR

    def load_current(self, serial: str):
        d = self._dir(serial)
        cur = d / _CURRENT_FILE
        if not cur.is_file():
            return None
        with open(cur, encoding="utf-8") as f:
            ptr = json.load(f)
        name = ptr.get("file") if isinstance(ptr, dict) and ptr.get("format") == self.CURRENT_FORMAT else None
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError(f"{_CURRENT_FILE} が不正")
        with open(d / name, encoding="utf-8") as f:
            return self._from_dict(json.load(f))

    def save(self, cal) -> Path:
        d = self._dir(cal.serial)
        d.mkdir(parents=True, exist_ok=True)
        stem = datetime.datetime.fromisoformat(cal.created_at).strftime("%Y%m%dT%H%M%S%z")
        body = json.dumps(cal.to_dict(), ensure_ascii=False, indent=2)
        n = 0
        while True:
            path = d / (f"{stem}.json" if n == 0 else f"{stem}-{n}.json")
            try:
                # "x" は既にあれば失敗するので、同じ日時でも上書きしない
                with open(path, "x", encoding="utf-8") as f:
                    f.write(body)
                break
            except FileExistsError:
                n += 1
        tmp = d / (_CURRENT_FILE + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"format": self.CURRENT_FORMAT, "version": 1, "file": path.name}, f)
        os.replace(tmp, d / _CURRENT_FILE)
        return path


# ---------------------------------------------------------------------------
# IQ の取り出し口と、機器の口の包み
# ---------------------------------------------------------------------------


class _TapHandle:
    """開いた機器（dll.RtlSdrHandle）の包み。読み取った 8bit の IQ を IqTapLib.sink へ見せる。

    中心を変えた直後（中心の設定・バッファの初期化の後の最初の読み）の先頭 16384 バイトは古い周波数のことが
    あるので見せない（機器の層が捨てるのと同じ範囲: 設計Q §6.9）。見せるのは機器スレッドの中。
    """

    def __init__(self, inner, tap: "IqTapLib") -> None:
        self._inner = inner
        self._tap = tap
        self._center: Optional[int] = None
        self._skip = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def set_center_freq(self, hz: int) -> None:
        self._inner.set_center_freq(hz)
        self._center = int(hz)
        self._skip = READ_BLOCK

    def reset_buffer(self) -> None:
        self._inner.reset_buffer()
        self._skip = READ_BLOCK

    def read(self, nbytes: int) -> np.ndarray:
        buf = self._inner.read(nbytes)
        data = buf[self._skip:] if self._skip else buf
        self._skip = 0
        sink = self._tap.sink
        if sink is not None and self._center is not None and data.size:
            try:
                sink(data, self._center)
            except Exception as e:  # noqa: BLE001 — 取り出し側の失敗で測定を止めない
                self._tap.last_error = repr(e)
        return buf


class IqTapLib:
    """DLL の口（dll.RtlSdrLib か試験の偽物）の包み。開いた機器を _TapHandle で包む。"""

    def __init__(self, lib) -> None:
        self._lib = lib
        # sink(8bit の IQ, いまのチューナーの中心 Hz)。機器スレッドから呼ぶ
        self.sink: Optional[Callable[[np.ndarray, int], None]] = None
        self.last_error: Optional[str] = None

    def __getattr__(self, name):
        return getattr(self._lib, name)

    def open(self, index: int):
        return _TapHandle(self._lib.open(index), self)


def align_to_axis(center_hz: int, sweep: AdoptedSweep, fmin: int, fmax: int) -> int:
    """チューナーの中心を出力の格子（sweep.axis）の点へ寄せる（機器の層は格子の上の中心だけを受け付ける）。"""
    start, step = sweep.axis.start_hz, sweep.axis.step_hz
    if step <= 0:
        return int(center_hz)
    k = (2 * (int(center_hz) - start) + step) // (2 * step)
    c = start + k * step
    while c < fmin:
        c += step
    while c > fmax:
        c -= step
    return c


class RtlSdrInstrumentDevice(Device):
    """本体が使う RTL-SDR の機器の口。中身は devices/rtlsdr の RtlSdrDevice のまま。

    - fixed_center_fn: 動かさない中心（Hz）か None を返す関数。測る直前に機器スレッドで呼び、
      出力の格子へ寄せて機器へ渡す（current_fixed に残す。IQ を見せるかの判断に使う）
    - 利得・ppm・段の一覧などは中身の機器の口をそのまま使う
    """

    fft_sdr = True

    def __init__(self, inner: rtl_driver.RtlSdrDevice, tap: IqTapLib) -> None:
        self.inner = inner
        self.tap = tap
        self.fixed_center_fn: Callable[[], Optional[int]] = lambda: None
        self.current_fixed: Optional[int] = None

    def __getattr__(self, name):
        if name in ("inner", "tap"):
            raise AttributeError(name)
        return getattr(self.inner, name)

    def profile(self) -> dict:
        return self.inner.profile()

    def identity(self):
        return self.inner.identity()

    def measure(self, sweep: AdoptedSweep, cancel=None) -> RawSweep:
        want = self.fixed_center_fn()
        fc = None
        if want is not None:
            f = self.inner.profile()["freq"]
            fc = align_to_axis(int(want), sweep, int(f["min_hz"]), int(f["max_hz"]))
        self.inner.fixed_center_hz = fc
        self.current_fixed = fc
        self.inner.progress_sink = self.progress_sink  # 進み具合は中身の機器の口が数える（設計J §4.13）
        return self.inner.measure(sweep, cancel)

    def fit_gain(self, sweep: AdoptedSweep, cancel=None, progress=None) -> dict:
        """利得を合わせる（設計Q §6.5）。中心を動かせないときは測るときと同じ中心で試す。"""
        want = self.fixed_center_fn()
        fc = None
        if want is not None:
            f = self.inner.profile()["freq"]
            fc = align_to_axis(int(want), sweep, int(f["min_hz"]), int(f["max_hz"]))
        self.inner.fixed_center_hz = fc
        self.current_fixed = fc
        return self.inner.fit_gain(sweep, cancel, progress)

    def close(self) -> None:
        self.tap.sink = None
        self.inner.close()


def open_rtlsdr_device(lib, port: Optional[str] = None, serial: Optional[str] = None,
                       profile: Optional[dict] = None) -> RtlSdrInstrumentDevice:
    """番号付きのポート（優先）かシリアル番号で開く。利得・ppm は開いた後に本体が入れ直す（_rtlsdr_prepare）。"""
    tap = IqTapLib(lib)
    dev = rtl_driver.RtlSdrDevice(profile, lib=tap)
    if port is not None:
        dev.open(port=port)
    else:
        dev.open(serial=serial)
    return RtlSdrInstrumentDevice(dev, tap)


def u8_to_iq(u8: np.ndarray) -> np.ndarray:
    """8bit の符号なし（I, Q の順。127.5 が 0）を complex64（振り切れが ±1.0）へ。rtl_tcp の出口の形と同じ。"""
    b = np.asarray(u8, dtype=np.uint8)
    if b.size % 2:
        b = b[:-1]
    x = (b.astype(np.float32) - np.float32(127.5)) / np.float32(127.5)
    return x.view(np.complex64)


def _now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat()


def _is_number(v) -> bool:
    return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)


# ---------------------------------------------------------------------------
# 本体に混ぜる部分
# ---------------------------------------------------------------------------


class RtlSdrMixin:
    """RTL-SDR の設定・校正・レベル・中心を動かせないときの扱い（Instrument に混ぜる）。"""

    # ------------------------------------------------------------------ 初期化・状態

    def _init_rtlsdr(self) -> None:
        cfg = self._settings.get("rtlsdr") or {}
        folder = cfg.get("dll_folder")
        gain = cfg.get("gain_db")
        self._rtlsdr_cfg = {
            "dll_folder": folder if isinstance(folder, str) and folder else None,
            "gain_db": float(gain) if _is_number(gain) else None,
            "agc": bool(cfg.get("agc", False)),
        }
        self._rtlsdr_cal_store = RtlSdrCalibrationStore(self._cal_store.root)
        self._rtlsdr_cal: Optional[RtlSdrCalibration] = None
        self._rtlsdr_unit: Optional[str] = None
        self._rtlsdr_overload_key: Optional[tuple] = None
        self._rtlsdr_overload_last = False
        self._rtlsdr_shared = 0
        # rtl_tcp の相手がつながっている間のチューナーの中心（Hz）。None は中心を動かしてよい
        self._rtl_tcp_center: Optional[int] = None
        self._rtlsdr_find_dll()
        self._rtlsdr_push_dll_folder()

    def _rtlsdr_find_dll(self) -> None:
        """DLL の場所とビット数（設定の画面が、見つけた場所と使えない理由を出す: 設計J §4.12）。"""
        path = find_dll(self._rtlsdr_cfg["dll_folder"])
        self._rtlsdr_dll_path = None if path is None else str(path)
        self._rtlsdr_dll_bits = dll_bits(path)

    def _rtlsdr_push_dll_folder(self) -> None:
        b = self._backends.get("rtlsdr")
        if b is not None and hasattr(b, "set_dll_folder"):
            b.set_dll_folder(self._rtlsdr_cfg["dll_folder"])

    def _is_rtlsdr(self) -> bool:
        return isinstance(self._device, RtlSdrInstrumentDevice)

    def _is_fft_sdr(self) -> bool:
        """IQ を FFT でスペクトルにする機器（RTL-SDR・PlutoSDR）か。中心を動かせないときの扱い（rtl_tcp の相手）と
        レベル・振り切れの扱いはこの2つで同じ。"""
        return bool(getattr(self._device, "fft_sdr", False))

    def _rtlsdr_device(self) -> RtlSdrInstrumentDevice:
        if self._conn != "connected" or not self._is_rtlsdr():
            raise CommandError("rtlsdr_not_connected")
        return self._device

    def _rtlsdr_state(self) -> dict:
        dev = self._device if self._is_rtlsdr() else None
        cal = self._rtlsdr_cal
        tgt = self._rtlsdr_fixed_target()
        return {
            "dll_folder": self._rtlsdr_cfg["dll_folder"],
            "dll_found": self._rtlsdr_dll_path is not None,
            "dll_path": self._rtlsdr_dll_path,
            "dll_bits": self._rtlsdr_dll_bits,
            "gain_db": self._rtlsdr_cfg["gain_db"],
            "agc": self._rtlsdr_cfg["agc"],
            "connected": dev is not None,
            "gain_db_actual": None if dev is None else dev.gain_db,
            "gain_steps_db": [] if dev is None else list(dev.gain_steps_db),
            "ppm": None if dev is None else dev.ppm,
            "calibration": None if cal is None else {
                "created_at": cal.created_at, "ppm": cal.ppm, "source": dict(cal.source),
                "level_offset_db": {str(k): v for k, v in cal.level_offset_db.items()},
            },
            "level_unit": self._rtlsdr_unit,
            "shared_serial": self._rtlsdr_shared if dev is not None else 0,
            "overload": self._rtlsdr_overload_last,
            # 「利得を合わせる」の最中か（ボタンを無効にする: 設計Q §6.5）
            "fitting": any(t.kind == "rtlsdr_fit_gain" for t in getattr(self, "_tasks", {}).values()),
            "fixed_center": None if tgt is None else {"cause": tgt[0], "tuner_center_hz": tgt[1]},
        }

    def _rtlsdr_gain_setting(self) -> Optional[float]:
        """いまの設定の利得（dB）。AGC なら None。未設定は機種の既定（設計Q §6.5）。"""
        if self._rtlsdr_cfg["agc"]:
            return None
        g = self._rtlsdr_cfg["gain_db"]
        if g is None:
            g = float(((self._profile.get("gain") or {}).get("default_db")) or 0.0)
        return g

    def _rtlsdr_save_settings(self) -> None:
        self._settings.set("rtlsdr", dict(self._rtlsdr_cfg))
        try:
            self._settings.save()
        except OSError as e:
            self._notice("settings_unwritable", {"message": str(e)})

    # ------------------------------------------------------------------ 接続・切断

    def _rtlsdr_prepare(self, dev: RtlSdrInstrumentDevice) -> None:
        """機器スレッドを始める前: 個体の校正（ppm）と利得を入れ、IQ の取り出し口をつなぐ（設計Q §6.6）。"""
        serial = dev.identity().serial
        self._rtlsdr_load_cal(serial)
        try:
            dev.set_ppm(self._rtlsdr_cal.ppm if self._rtlsdr_cal else 0)
            dev.set_gain(self._rtlsdr_gain_setting())
        except Exception as e:  # noqa: BLE001 — 入れられなくても測れるので知らせて続ける
            self._notice("rtlsdr_setting_failed", {"message": str(e)})
        dev.fixed_center_fn = self._rtlsdr_fixed_center
        dev.tap.sink = self._rtlsdr_make_sink(dev)
        self._rtlsdr_unit = None
        self._rtlsdr_overload_key = None
        self._rtlsdr_overload_last = False
        self._rtlsdr_shared = int(getattr(dev, "shared_serial", 1) or 1)
        if self._rtlsdr_shared > 1:
            # 同じシリアル番号の機器が複数: 校正はシリアル番号に紐づくので共有になる（設計Q §6.6）
            self._notice("rtlsdr_shared_serial", {"serial": serial, "count": self._rtlsdr_shared,
                                                  "calibration_shared": True})

    def _rtlsdr_load_cal(self, serial: str) -> None:
        try:
            self._rtlsdr_cal = self._rtlsdr_cal_store.load_current(serial)
        except Exception as e:  # noqa: BLE001 — 読めない校正は「無し」として知らせる
            self._rtlsdr_cal = None
            self._notice("calibration_unreadable", {"message": str(e), "kind": "rtlsdr"})

    def _rtlsdr_on_drop(self) -> None:
        self._rtl_tcp_center = None
        self._rtlsdr_overload_last = False

    # ------------------------------------------------------------------ 1回の掃引（レベル）

    def _build_rtlsdr_frame(self, raw: RawSweep) -> Frame:
        """生の値 → レベル（dB）→ トレースに渡すリニアの値（設計Q §6.4・levels.py）。"""
        a = raw.adopted
        ex = raw.extra or {}
        n = a.axis.points
        gain = ex.get("gain_db")
        offset = None
        if self._rtlsdr_cal is not None and gain is not None:
            offset = rtl_levels.offset_for_gain(self._rtlsdr_cal.level_offset_db, int(round(gain * 10)))
        level, unit = rtl_levels.to_level(raw.raw, gain, offset)
        # PlutoSDR の受信の利得の補正（設計Q §7.6: 周波数と利得ごと。単位は変わらない）。表が無ければ None
        gain_corr = self._pluto_gain_offset(a.axis.freqs(), gain)
        if gain_corr is not None:
            level = level + gain_corr
            # 当てた補正を記録にも残す（再生で同じ値に戻す: 設計Q §7.6。表は記録に入らないので点ごとの値で持つ）
            raw.extra["gain_corr_db"] = [round(float(x), 3) for x in np.asarray(gain_corr, dtype=float)]
        else:
            raw.extra.pop("gain_corr_db", None)
        no_value = np.zeros(n, dtype=bool)
        nv = (ex.get("flags") or {}).get("no_value")
        if nv is not None:
            nv = np.asarray(nv, dtype=bool)
            no_value[: min(n, nv.size)] = nv[:n]
        level = np.where(no_value, np.nan, level)
        self._no_calibration = unit != rtl_levels.UNIT_DBM
        self._rtlsdr_unit = unit
        self._rtlsdr_check_overload(a, ex)
        prof = dict(self._profile)
        prof["level_unit"] = unit
        # どの窓で測ったか。トレースはこの写しを持つので、マーカーの読み値の注意（Hann の格子の間の
        # 読み落ち: 設計Q §6.4）をトレースごとに決められる
        prof["fft_window"] = ex.get("window")
        prof["gain_correction"] = gain_corr is not None
        return Frame(
            axis=a.axis,
            values=rtl_levels.to_linear(level),
            quantity=Quantity.POWER,
            value_kind=ValueKind.REAL,
            time=raw.started_at,
            duration_s=raw.duration_s,
            sweep_no=self._sweep_count,
            display_start_hz=a.start_hz,
            display_stop_hz=a.stop_hz,
            adopted=a,
            raw=raw,
            flags={"transient": np.zeros(n, dtype=bool), "no_value": no_value},
            profile=prof,
        )

    def _rtlsdr_check_overload(self, a: AdoptedSweep, ex: dict) -> None:
        """振り切れ（設計Q §6.4）は条件（掃引条件・利得）ごとに1回だけ知らせ、状態には毎回の有無を出す。"""
        over = bool(ex.get("overload"))
        self._rtlsdr_overload_last = over
        if not over:
            return
        key = (a.start_hz, a.stop_hz, a.axis, a.rbw_hz, ex.get("gain_db"), ex.get("gain_mode"))
        if key == self._rtlsdr_overload_key:
            return
        self._rtlsdr_overload_key = key
        self._notice("rtlsdr_overload", {"ratio": float(ex.get("overload_ratio_max") or 0.0),
                                         "gain_db": ex.get("gain_db")})

    def _rtlsdr_changed_meaning(self, cause: str) -> None:
        """利得・ppm・レベルの補正を変えた: 値の意味が変わるので蓄積を捨て、測っている途中の結果も捨てる
        （設計Q §6.5・設計F §5.3）。"""
        self._on_condition(ConditionChange.CALIBRATION, cause=cause)
        self._rtlsdr_overload_key = None
        self._push_job()

    # ------------------------------------------------------------------ 命令

    def _cmd_rtlsdr_set(self, dll_folder=_UNSET, gain_db=_UNSET, agc=_UNSET, ppm=_UNSET) -> dict:
        """DLL のフォルダ・受信の利得（手動の dB か AGC）・ppm を変える（設計Q §6.2・§6.5・§6.6）。

        DLL のフォルダと利得は設定（settings.json の `rtlsdr`）にすぐ残す。ppm は個体の校正として
        つながっている機器のシリアル番号の下へ保存する（つながっていなければ受け付けない）。
        """
        if dll_folder is not _UNSET and dll_folder is not None and not isinstance(dll_folder, str):
            raise CommandError("bad_value", "dll_folder")
        if gain_db is not _UNSET and gain_db is not None:
            if not _is_number(gain_db) or not GAIN_MIN_DB <= gain_db <= GAIN_MAX_DB:
                raise CommandError("bad_value", "gain_db", {"min": GAIN_MIN_DB, "max": GAIN_MAX_DB})
        if agc is not _UNSET and not isinstance(agc, bool):
            raise CommandError("bad_value", "agc")
        if agc is True and gain_db is not _UNSET and gain_db is not None:
            raise CommandError("bad_value", "agc", {"conflict": "gain_db"})
        if ppm is not _UNSET:
            if isinstance(ppm, bool) or not isinstance(ppm, int):
                raise CommandError("not_integer", "ppm")
            if abs(ppm) > PPM_LIMIT:
                raise CommandError("bad_value", "ppm", {"min": -PPM_LIMIT, "max": PPM_LIMIT})
            self._rtlsdr_device()
        if dll_folder is not _UNSET:
            self._rtlsdr_cfg["dll_folder"] = dll_folder or None
            self._rtlsdr_find_dll()
            self._rtlsdr_push_dll_folder()
        if gain_db is not _UNSET or agc is not _UNSET:
            if gain_db is not _UNSET and gain_db is not None:
                self._rtlsdr_cfg["gain_db"] = float(gain_db)
                self._rtlsdr_cfg["agc"] = False
            if agc is not _UNSET:
                self._rtlsdr_cfg["agc"] = agc
            self._rtlsdr_apply_gain()
        if ppm is not _UNSET:
            self._rtlsdr_set_ppm(ppm, {"kind": "manual"})
        if dll_folder is not _UNSET or gain_db is not _UNSET or agc is not _UNSET:
            self._rtlsdr_save_settings()
        self._changed()
        return {"rtlsdr": self._rtlsdr_state()}

    def _rtlsdr_apply_gain(self) -> None:
        """設定の利得をつながっている機器へ入れる。入れた後の段が変わったときだけ蓄積を捨てる。"""
        if not self._is_rtlsdr():
            return
        dev = self._device
        before = (dev.gain_db, dev.gain_db is None)
        want = self._rtlsdr_gain_setting()
        dev.set_gain(want)
        if want is None:
            after = (None, True)
        else:
            steps = list(dev.gain_steps_db) or [want]
            after = (min(steps, key=lambda g: (abs(g - want), g)), False)
        if after != before:
            self._rtlsdr_changed_meaning("gain")

    def _cmd_rtlsdr_fit_gain(self) -> dict:
        """「利得を合わせる」（設計Q §6.5）: いまの条件で振り切れないいちばん大きい段を選んで入れる（長い処理）。

        段を上から短く測って試すので、位置の多い広い Span では時間がかかる（H §4.1: 進み具合とやめるを出す）。
        終わったら設定に残し、値の意味が変わるので蓄積を捨てる（設計F §5.3）。知らせは task_done の1件だけで、
        選んだ段と捨てたトレースを入れる（trace_reset を別に出さない）。AGC の間は受け付けない（段を選ぶ意味が無い）。
        """
        dev = self._rtlsdr_device()
        if self._rtlsdr_cfg["agc"]:
            raise CommandError("rtlsdr_fit_needs_manual_gain")

        def done(res: dict) -> None:
            self._rtlsdr_cfg["gain_db"] = float(res["gain_db"])
            self._rtlsdr_cfg["agc"] = False
            self._rtlsdr_save_settings()
            dev.set_gain(float(res["gain_db"]))

        return self._sdr_start_fit_gain("rtlsdr_fit_gain", dev, done, len(dev.gain_steps_db))

    def _sdr_start_fit_gain(self, kind: str, dev, on_done: Callable[[dict], None], total: int) -> dict:
        """「利得を合わせる」の長い処理（RTL-SDR・PlutoSDR で同じ: 設計Q §6.5）。on_done は選んだ段を設定に残す。"""
        if any(t.kind in ("rtlsdr_fit_gain", "plutosdr_fit_gain") for t in self._tasks.values()):
            raise CommandError("rtlsdr_fit_running")
        sweep = self._adopted
        previous = dev.gain_db

        def submit(progress, cancel):
            fut: concurrent.futures.Future = concurrent.futures.Future()

            def run() -> None:
                t0 = time.monotonic()
                try:
                    fut.set_result(dev.fit_gain(sweep, cancel,
                                                lambda d, t: progress(d, t, time.monotonic() - t0)))
                except SweepDiscarded as e:
                    # やめた（試す前の段に戻してある）は task_cancelled、それ以外は task_failed
                    fut.set_exception(Cancelled() if e.reason == "cancelled" else e)
                except BaseException as e:  # noqa: BLE001 — 終わりの知らせ（task_failed）にする
                    fut.set_exception(e)

            threading.Thread(target=run, name="pygigast-rtl-fit", daemon=True).start()
            return fut

        def then(res: dict) -> dict:
            on_done(res)
            resets = self._traces.on_condition_change(ConditionChange.CALIBRATION)
            self._diff_refresh()
            self._rtlsdr_overload_key = None
            # 前の段の掃引の「振り切れ」は古い。合わせた段で試した結果に置き換える（次の掃引で測り直す）
            self._rtlsdr_overload_last = bool(res.get("overload"))
            self._push_job()
            return {**res, "previous_gain_db": previous,
                    "traces_reset": sorted(resets), "codes": {str(k): v for k, v in resets.items()}}

        return self._start_task(kind, submit, then, total=total, detail={"gain_before_db": previous})

    def _rtlsdr_set_ppm(self, ppm: int, source: dict) -> None:
        dev = self._rtlsdr_device()
        self._rtlsdr_save_cal(source, ppm=int(ppm))
        dev.set_ppm(int(ppm))
        self._rtlsdr_changed_meaning("ppm")

    def _rtlsdr_save_cal(self, source: dict, **changes) -> RtlSdrCalibration:
        ident = self._identity or {}
        serial = str(ident.get("serial") or "")
        base = self._rtlsdr_cal
        if base is None or base.serial != serial:
            base = RtlSdrCalibration(model=str(self._profile.get("model")), serial=serial, created_at=_now_iso(),
                                     source={})
        new = dataclasses.replace(base, created_at=_now_iso(), source=dict(source), **changes)
        try:
            self._rtlsdr_cal_store.save(new)
        except (OSError, ValueError) as e:
            raise CommandError("calibration_unwritable", None, {"message": str(e)}) from e
        self._rtlsdr_cal = new
        return new

    def _rtlsdr_marker_row(self, marker_id) -> dict:
        """マーカーの読み値（周波数・レベル）。RTL-SDR で測ったトレースの上で値があること。"""
        m = self._marker(marker_id)
        row = next((r for r in self._marker_snap.get("markers", []) if r.get("id") == m.id), None)
        if row is None or row.get("status") != "ok" or row.get("freq_hz") is None or row.get("level") is None:
            raise CommandError("marker_no_value", "marker_id", {"id": m.id})
        t = self._traces[int(row["trace"])]
        if ((t.profile or {}).get("raw") or {}).get("encoding") != RTLSDR_ENCODING:
            raise CommandError("marker_not_on_rtlsdr_trace", "marker_id", {"id": m.id})
        return row

    def _rtlsdr_last_extra(self) -> dict:
        raw = self._last_raw
        return (raw.extra or {}) if raw is not None and raw.encoding == RTLSDR_ENCODING else {}

    def _cmd_rtlsdr_calibrate_ppm(self, marker_id, true_freq_hz) -> dict:
        """マーカーを置いた既知の周波数の信号から ppm を計算して保存する（設計Q §6.6）。

        いまの補正 p0 で測った周波数 f_meas に、本当の周波数 f_true の信号が見えたとき、
        水晶のずれは p0 + (f_true − f_meas) / f_true × 10⁶ ppm（ずれが小さいときの近似）。
        """
        dev = self._rtlsdr_device()
        self._check_hz("true_freq_hz", true_freq_hz)
        if true_freq_hz <= 0:
            raise CommandError("bad_value", "true_freq_hz")
        row = self._rtlsdr_marker_row(marker_id)
        measured = int(row["freq_hz"])
        p0 = self._rtlsdr_last_extra().get("ppm")
        p0 = dev.ppm if p0 is None else int(p0)
        new = int(round(p0 + (true_freq_hz - measured) / true_freq_hz * 1e6))
        if abs(new) > PPM_LIMIT:
            raise CommandError("bad_value", "true_freq_hz", {"ppm": new, "limit": PPM_LIMIT})
        self._rtlsdr_set_ppm(new, {"kind": "marker_ppm", "measured_hz": measured, "true_freq_hz": true_freq_hz,
                                   "previous_ppm": p0})
        self._changed()
        return {"ppm": new, "previous_ppm": p0, "measured_hz": measured, "true_freq_hz": true_freq_hz}

    def _cmd_rtlsdr_calibrate_level(self, marker_id, true_dbm) -> dict:
        """マーカーを置いた既知のレベルの信号から、いまの利得の段のレベルの補正を保存する（設計Q §6.6）。"""
        self._rtlsdr_device()
        if not _is_number(true_dbm):
            raise CommandError("bad_value", "true_dbm")
        gain = self._rtlsdr_last_extra().get("gain_db")
        if gain is None:
            # AGC の間は利得が分からず、段ごとの補正を決められない（レベルは dBFS: 設計Q §6.9）
            raise CommandError("rtlsdr_level_needs_manual_gain")
        row = self._rtlsdr_marker_row(marker_id)
        tenth = int(round(float(gain) * 10))
        old = None
        if self._rtlsdr_cal is not None:
            old = rtl_levels.offset_for_gain(self._rtlsdr_cal.level_offset_db, tenth)
        measured = float(row["level"])
        new_off = (old or 0.0) + (float(true_dbm) - measured)
        offsets = {} if self._rtlsdr_cal is None else dict(self._rtlsdr_cal.level_offset_db)
        offsets[tenth] = round(new_off, 3)
        self._rtlsdr_save_cal({"kind": "marker_level", "gain_db": gain, "measured": measured,
                               "true_dbm": float(true_dbm)}, level_offset_db=offsets)
        self._rtlsdr_changed_meaning("level_calibration")
        self._changed()
        return {"gain_db": gain, "level_offset_db": offsets[tenth], "previous_offset_db": old,
                "measured": measured, "true_dbm": float(true_dbm)}

    # ------------------------------------------------------------------ 中心を動かせないとき（設計Q §6.4・§6.7・§6.8）

    def _rtlsdr_fixed_target(self) -> Optional[tuple[str, int]]:
        """(理由, チューナーの中心 Hz)。聞いている間が優先。どちらでもなければ None。"""
        lst = self._listen
        if lst is not None:
            return CAUSE_LISTEN, lst["tuner_center_hz"]
        if self._rtl_tcp_center is not None:
            return CAUSE_RTLTCP, self._rtl_tcp_center
        return None

    def _rtlsdr_fixed_center(self) -> Optional[int]:
        """機器スレッドから呼ぶ（測る直前）。制御スレッドが置き換える値を読むだけ。"""
        t = self._rtlsdr_fixed_target()
        return None if t is None else t[1]

    def _rtlsdr_grid_half(self, rbw_hz: int) -> tuple[int, int]:
        """(格子 Hz, 使える幅の半分 h Hz)（設計Q §6.4）。"""
        p = rtl_driver.fft_params(self._profile, rbw_hz)
        return p.grid, p.half

    def _rtlsdr_range(self) -> tuple[int, int]:
        f = self._profile["freq"]
        return int(f["min_hz"]), int(f["max_hz"])

    def _rtlsdr_check_condition(self, new: AdoptedSweep) -> None:
        """中心を動かせない間の条件の変更（_apply・元に戻すの前に呼ぶ）。

        使える幅（チューナーの中心 ±h）に収まれば受け付ける。rtl_tcp の相手がいる間は、Span が使える幅
        （2h）以下なら中心を新しい表示の中心へ動かして受け付ける（相手の IQ もその中心になる）。
        それ以外（継ぎ合わせが要る条件）は理由付きで受け付けない（設計Q §6.7。U-08）。
        """
        if not self._is_fft_sdr():
            return
        tgt = self._rtlsdr_fixed_target()
        if tgt is None:
            return
        cause, c = tgt
        grid, h = self._rtlsdr_grid_half(new.rbw_hz)
        if new.axis.start_hz >= c - h and new.axis.last_hz <= c + h:
            return
        if cause == CAUSE_RTLTCP and new.axis.last_hz - new.axis.start_hz <= 2 * h:
            fmin, fmax = self._rtlsdr_range()
            self._rtl_tcp_center = min(max(new.center_hz, fmin), fmax)
            return
        raise CommandError("rtlsdr_stitching_blocked", "span_hz",
                           {"cause": cause, "max_span_hz": 2 * h, "tuner_center_hz": c})

    def _rtlsdr_window(self, c: int, half_span: Optional[int] = None) -> tuple[int, int]:
        """中心 c のまわりの使える幅（格子の倍数で ±m 格子）。機種の範囲の中に収める。"""
        a = self._adopted
        grid, h = self._rtlsdr_grid_half(a.rbw_hz)
        want = h if half_span is None else min(half_span, h)
        min_span = int(self._profile["freq"].get("min_span_hz", grid))
        m = max(want // grid, -(-min_span // (2 * grid)), 1)
        fmin, fmax = self._rtlsdr_range()
        lo = c - min(m, max(0, (c - fmin) // grid)) * grid
        hi = c + min(m, max(0, (fmax - c) // grid)) * grid
        return lo, hi

    def _rtlsdr_fit_display(self, c: int, cause: str, record_undo: bool) -> Optional[dict]:
        """表示の範囲がチューナーの中心 c の使える幅に収まらなければ、c のまわりの使える幅に変えて知らせる。"""
        a = self._adopted
        grid, h = self._rtlsdr_grid_half(a.rbw_hz)
        if a.axis.start_hz >= c - h and a.axis.last_hz <= c + h:
            return None
        lo, hi = self._rtlsdr_window(c)
        # チューナーの使える幅はアプリが決める範囲。Span 固定の規則を通さず、そのまま使う（C §5.12 の one_shot）
        req = SweepRequest(start_hz=lo, stop_hz=hi, edited=("start_hz", "stop_hz"), one_shot=True)
        if record_undo:
            out = self._apply(req)
        else:
            try:
                new = self._model.apply(req, self._adopted)
            except (ValueError, TypeError) as e:
                raise CommandError("bad_value", None, {"message": str(e)}) from e
            self._set_adopted(new)
            out = {"adjustments": [dataclasses.asdict(x) for x in new.adjustments]}
        self._notice("rtlsdr_span_limited", {
            "cause": cause, "tuner_center_hz": c, "previous": {"start_hz": a.start_hz, "stop_hz": a.stop_hz},
            "start_hz": self._adopted.start_hz, "stop_hz": self._adopted.stop_hz,
        })
        return out

    # ------------------------------------------------------------------ rtl_tcp（設計Q §6.7）

    def _rtlsdr_on_clients(self) -> None:
        """相手の数が変わった: つながっている間はチューナーの中心を動かさない（掃引をしない）。"""
        s = self._rtl_server
        n = s.client_count if (s is not None and s.running) else 0
        if not self._is_fft_sdr():
            self._rtl_tcp_center = None
            return
        if n > 0 and self._rtl_tcp_center is None:
            if self._listen is not None:
                self._rtl_tcp_center = self._listen["tuner_center_hz"]
            else:
                c = self._adopted.center_hz
                self._rtl_tcp_center = c
                try:
                    self._rtlsdr_fit_display(c, CAUSE_RTLTCP, record_undo=True)
                except CommandError as e:
                    self._notice("rtlsdr_span_limit_failed", {"code": e.code})
            self._push_job()
        elif n == 0 and self._rtl_tcp_center is not None:
            self._rtl_tcp_center = None
            self._push_job()

    def _rtlsdr_tcp_set_freq(self, freq_hz: int) -> None:
        """相手の周波数の命令: その周波数をチューナーの中心にしたリアルタイムの動きへ（設計Q §6.7）。"""
        if self._listen is not None:
            # 聞いている間はマーカーの位置でチューナーを決めている。相手の命令では動かさない
            self._notice("rtltcp_freq_from_client", {"freq_hz": freq_hz, "applied": False, "code": CAUSE_LISTEN})
            return
        fmin, fmax = self._rtlsdr_range()
        f = min(max(int(freq_hz), fmin), fmax)
        old = self._rtl_tcp_center
        if f == old:
            return  # 同じ周波数の繰り返し（外のソフトは同じ値を何度も送る）は知らせない
        a = self._adopted
        self._rtl_tcp_center = f
        lo, hi = self._rtlsdr_window(f, a.span_hz // 2)
        try:
            # 外のソフトが決めた中心の使える幅。Span 固定の規則を通さず、そのまま使う（C §5.12 の one_shot）
            out = self._apply(SweepRequest(start_hz=lo, stop_hz=hi, edited=("start_hz", "stop_hz"), one_shot=True))
        except CommandError as e:
            self._rtl_tcp_center = old
            self._notice("rtltcp_freq_from_client", {"freq_hz": freq_hz, "applied": False, "code": e.code})
            return
        self._push_job()
        b = self._adopted
        self._notice("rtltcp_freq_from_client", {
            "freq_hz": freq_hz, "applied": True, "center_hz": b.center_hz, "span_hz": b.span_hz,
            "tuner_center_hz": f, "span_limited": b.span_hz < a.span_hz,
            "adjustments": out.get("adjustments", []),
        })

    def _rtlsdr_tcp_command(self, name: str, value) -> None:
        """相手のサンプリング速度・利得の命令（設計Q §6.7: できるものは従う）。"""
        fs = int(self._profile["fft"]["sample_rate_hz"])
        if name == "set_sample_rate":
            if value != fs:
                # FFT の格子は 2.048MS/s から決めている（設計Q §6.3・§6.4）。ほかの速さには従わない
                self._notice("rtltcp_sample_rate_unsupported", {"requested": value, "used": fs})
            return
        dev = self._device
        want = _UNSET
        if name == "set_gain_mode":
            if value == 0:
                want = None
            elif self._rtlsdr_cfg["agc"]:
                want = self._rtlsdr_cfg["gain_db"] if self._rtlsdr_cfg["gain_db"] is not None else \
                    float(self._profile["gain"]["default_db"])
        elif name == "set_gain" and isinstance(value, int):
            want = value / 10.0
        elif name == "set_gain_by_index" and isinstance(value, int):
            steps = list(dev.gain_steps_db)
            if 0 <= value < len(steps):
                want = steps[value]
        # 周波数のずれ（ppm）は個体の校正で決めているので従わない。それ以外の命令も使わない
        if want is _UNSET:
            return
        before = (self._rtlsdr_cfg["gain_db"], self._rtlsdr_cfg["agc"])
        if want is None:
            self._rtlsdr_cfg["agc"] = True
        else:
            self._rtlsdr_cfg["gain_db"] = float(min(max(want, GAIN_MIN_DB), GAIN_MAX_DB))
            self._rtlsdr_cfg["agc"] = False
        if (self._rtlsdr_cfg["gain_db"], self._rtlsdr_cfg["agc"]) == before:
            return
        self._rtlsdr_apply_gain()
        self._rtlsdr_save_settings()
        self._notice("rtltcp_gain_from_client", {"gain_db": self._rtlsdr_cfg["gain_db"], "agc": self._rtlsdr_cfg["agc"]})
        self._changed()

    def _rtlsdr_rtltcp_header(self) -> tuple[int, int]:
        """rtl_tcp の先頭の中身（チューナーの種類・利得の段数）。機器から読んだ値（設計Q §6.7）。"""
        prof = self._device.profile()
        steps = (prof.get("gain") or {}).get("steps_tenth_db") or []
        return int(prof.get("tuner_type") or TUNER_R820T), len(steps)

    # ------------------------------------------------------------------ IQ の取り出し（機器スレッド）

    def _rtlsdr_make_sink(self, dev: RtlSdrInstrumentDevice) -> Callable[[np.ndarray, int], None]:
        fs = int(dev.profile()["fft"]["sample_rate_hz"])

        def sink(u8: np.ndarray, center: int) -> None:
            fc = dev.current_fixed
            if fc is None or center != fc:
                return  # 中心を動かす測定（継ぎ合わせ）の IQ は外へ出さない
            rt = self._listen_rt
            s = self._rtl_server
            to_tcp = s is not None and s.running and s.client_count > 0
            if not to_tcp and rt is None:
                return
            iq = u8_to_iq(u8)
            if to_tcp:
                self.push_device_iq(iq, fs)
            if rt is not None:
                self._listen_feed(rt, iq, center, fs)

        return sink
