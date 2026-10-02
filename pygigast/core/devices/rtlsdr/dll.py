"""`rtlsdr.dll` を ctypes で直接呼ぶ薄い部品（設計Q §6.2・§6.9）。

- 使う関数は16個だけ（数・名前・USB の文字列（開く前／開いた後）・開く・閉じる・チューナーの種類・
  利得の一覧・サンプリング速度・中心周波数・利得の手動・利得・AGC・ppm の設定と読み・
  バッファの初期化・同期読み）。pyrtlsdr は手元の Blog 版の DLL と合わないので使わない（設計Q §4.2.1）
- DLL はアプリに同梱しない。探す順は ①設定で選んだフォルダ ②アプリのフォルダの `rtlsdr/` ③PATH（設計Q §6.2）
- 戻り値の負の数は例外にする。USB の失敗（libusb の入出力・機器なし・時間切れなど）は DeviceDisconnected
- DLL は標準エラーに直接書く（"Found Rafael Micro R820T tuner" など）。アプリの記録には混ぜない（設計Q §6.9）
"""
from __future__ import annotations

import ctypes as C
import ctypes.util
import os
import sys
import threading
from pathlib import Path
from typing import Optional, Sequence, Union

import numpy as np

from ...types import DeviceDisconnected, DeviceError

DLL_NAME = "rtlsdr.dll" if sys.platform == "win32" else "librtlsdr.so"

# 同期読みの大きさの単位（バイト）。読みはこの倍数に固定する（設計Q §6.9: I と Q の順がずれないかを
# 確かめていない大きさは使わない）
READ_BLOCK = 16384

# libusb の失敗のコード（DLL が同期読み・レジスタの読み書きの失敗をそのまま返す）。
# 入出力の失敗・機器が無い・見つからない・時間切れ・パイプの失敗は「切れた」として扱う
_LIBUSB_DISCONNECT = {-1: "LIBUSB_ERROR_IO", -4: "LIBUSB_ERROR_NO_DEVICE", -5: "LIBUSB_ERROR_NOT_FOUND",
                      -7: "LIBUSB_ERROR_TIMEOUT", -9: "LIBUSB_ERROR_PIPE"}

# ppm に今と同じ値を入れると返る値。失敗ではない（設計Q §6.9 落とし穴②）
_PPM_UNCHANGED = -2


class DllNotFound(DeviceError):
    """RTL-SDR の DLL が見つからない。機器の一覧に RTL-SDR を出さない（GigaSt は使える: 設計Q §6.2）。"""


class RtlSdrError(DeviceError):
    """DLL の関数が失敗を返した（切れたのではないもの）。"""

    def __init__(self, func: str, code: int) -> None:
        super().__init__(f"rtlsdr_{func} が {code} を返した")
        self.func = func
        self.code = code


def _raise_for(func: str, code: int) -> None:
    if code in _LIBUSB_DISCONNECT:
        raise DeviceDisconnected(f"rtlsdr_{func} が {code}（{_LIBUSB_DISCONNECT[code]}）を返した")
    raise RtlSdrError(func, code)


def app_dir() -> Path:
    """アプリのフォルダ。凍結した配布物では実行ファイルのフォルダ、ソースからはパッケージの1つ上。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[4]


def candidate_dirs(folder: Optional[Union[str, Path]] = None) -> list[Path]:
    """DLL を探すフォルダの並び（PATH は含まない。PATH は find_dll が最後に見る）。"""
    dirs: list[Path] = []
    if folder:
        dirs.append(Path(folder))
    dirs.append(app_dir() / "rtlsdr")
    return dirs


def find_dll(folder: Optional[Union[str, Path]] = None) -> Optional[Path]:
    """DLL の場所。見つからなければ None（設計Q §6.2 の順）。"""
    for d in candidate_dirs(folder):
        p = d / DLL_NAME
        if p.is_file():
            return p
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if d and (Path(d) / DLL_NAME).is_file():
            return Path(d) / DLL_NAME
    found = ctypes.util.find_library("rtlsdr")
    return Path(found) if found and Path(found).is_file() else None


# Windows の DLL の頭（PE）の機械の種類 → ビット数
_PE_MACHINE_BITS = {0x8664: 64, 0xAA64: 64, 0x014C: 32}


def dll_bits(path: Optional[Union[str, Path]]) -> Optional[int]:
    """DLL が 32 ビット版か 64 ビット版か（ファイルの頭を読むだけで、読み込まない）。分からなければ None。

    64 ビットの Python は 32 ビット版の DLL を読めない。見つけたことだけで「使える」と言わないために確かめる
    （設計J §4.12: 設定の画面は見つけた場所と、使えないならその理由を出す）。
    """
    if not path:
        return None
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return None
    if len(head) < 0x40 or head[:2] != b"MZ":
        return None
    pe = int.from_bytes(head[0x3C:0x40], "little")
    if pe + 6 > len(head) or head[pe:pe + 4] != b"PE\0\0":
        return None
    return _PE_MACHINE_BITS.get(int.from_bytes(head[pe + 4:pe + 6], "little"))


_dev_p = C.c_void_p

# (名前, 戻り値の型, 引数の型)。名前は rtlsdr_ を除いたもの
_SIGNATURES: Sequence[tuple[str, object, list]] = (
    ("get_device_count", C.c_uint32, []),
    ("get_device_name", C.c_char_p, [C.c_uint32]),
    ("get_device_usb_strings", C.c_int, [C.c_uint32, C.c_char_p, C.c_char_p, C.c_char_p]),
    ("open", C.c_int, [C.POINTER(_dev_p), C.c_uint32]),
    ("close", C.c_int, [_dev_p]),
    ("get_usb_strings", C.c_int, [_dev_p, C.c_char_p, C.c_char_p, C.c_char_p]),
    ("get_tuner_type", C.c_int, [_dev_p]),
    ("get_tuner_gains", C.c_int, [_dev_p, C.POINTER(C.c_int)]),
    ("set_sample_rate", C.c_int, [_dev_p, C.c_uint32]),
    ("set_center_freq", C.c_int, [_dev_p, C.c_uint32]),
    ("set_tuner_gain_mode", C.c_int, [_dev_p, C.c_int]),
    ("set_tuner_gain", C.c_int, [_dev_p, C.c_int]),
    ("set_agc_mode", C.c_int, [_dev_p, C.c_int]),
    ("set_freq_correction", C.c_int, [_dev_p, C.c_int]),
    ("get_freq_correction", C.c_int, [_dev_p]),
    ("reset_buffer", C.c_int, [_dev_p]),
    ("read_sync", C.c_int, [_dev_p, C.c_void_p, C.c_int, C.POINTER(C.c_int)]),
)

# DLL は1つのプロセスで1回だけ読む（フォルダごと）
_loaded: dict[Path, "RtlSdrLib"] = {}
_load_lock = threading.Lock()


class RtlSdrLib:
    """読み込んだ DLL と、使う16個の関数。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        if sys.platform == "win32":
            # 同じフォルダの依存 DLL（pthreadVC2.dll など）を見つけられるようにする
            self._dll_dir = os.add_dll_directory(str(self.path.parent))
        try:
            self._lib = C.CDLL(str(self.path))
        except OSError as e:
            raise DllNotFound(f"RTL-SDR の DLL を読み込めない: {e}") from e
        self._f = {}
        for name, res, args in _SIGNATURES:
            try:
                f = getattr(self._lib, "rtlsdr_" + name)
            except AttributeError as e:
                raise DllNotFound(f"DLL に rtlsdr_{name} が無い") from e
            f.restype = res
            f.argtypes = args
            self._f[name] = f

    @classmethod
    def load(cls, folder: Optional[Union[str, Path]] = None) -> "RtlSdrLib":
        path = find_dll(folder)
        if path is None:
            raise DllNotFound("RTL-SDR の DLL が見つかりません")
        path = path.resolve()
        with _load_lock:
            if path not in _loaded:
                _loaded[path] = cls(path)
            return _loaded[path]

    # ---- 開く前 ----
    def device_count(self) -> int:
        return int(self._f["get_device_count"]())

    def device_name(self, index: int) -> str:
        v = self._f["get_device_name"](index)
        return v.decode(errors="replace") if v else ""

    def device_usb_strings(self, index: int) -> tuple[str, str, str]:
        """開く前の USB の文字列（製造者・製品・シリアル）。開いている機器では −3 で失敗する（設計Q §6.9）。"""
        m, p, s = (C.create_string_buffer(256) for _ in range(3))
        r = self._f["get_device_usb_strings"](index, m, p, s)
        if r != 0:
            _raise_for("get_device_usb_strings", r)
        return (m.value.decode(errors="replace"), p.value.decode(errors="replace"),
                s.value.decode(errors="replace"))

    # ---- 開く・閉じる ----
    def open(self, index: int) -> "RtlSdrHandle":
        d = _dev_p()
        r = self._f["open"](C.byref(d), index)
        if r != 0 or not d.value:
            # 2重に開くと −3。先に開いた側は影響を受けない（設計Q §6.9）
            _raise_for("open", r if r != 0 else -1)
        return RtlSdrHandle(self, d)


class RtlSdrHandle:
    """開いた機器1台。スレッドをまたいで同時に呼ばない（機器スレッドだけが使う: 設計Q §6.2）。"""

    def __init__(self, lib: RtlSdrLib, dev: C.c_void_p) -> None:
        self._lib = lib
        self._f = lib._f
        self._dev = dev

    def _call(self, name: str, *args) -> int:
        if self._dev is None:
            raise DeviceDisconnected("閉じている")
        return int(self._f[name](self._dev, *args))

    def _ok(self, name: str, *args) -> int:
        r = self._call(name, *args)
        if r < 0:
            _raise_for(name, r)
        return r

    @property
    def is_open(self) -> bool:
        return self._dev is not None

    def close(self) -> None:
        if self._dev is not None:
            dev, self._dev = self._dev, None
            self._f["close"](dev)

    def usb_strings(self) -> tuple[str, str, str]:
        """開いた後の USB の文字列（製造者・製品・シリアル）。"""
        m, p, s = (C.create_string_buffer(256) for _ in range(3))
        self._ok("get_usb_strings", m, p, s)
        return (m.value.decode(errors="replace"), p.value.decode(errors="replace"),
                s.value.decode(errors="replace"))

    def tuner_type(self) -> int:
        return self._call("get_tuner_type")

    def tuner_gains_tenth_db(self) -> list[int]:
        """利得の段（0.1dB 単位）。"""
        n = self._ok("get_tuner_gains", C.cast(None, C.POINTER(C.c_int)))
        buf = (C.c_int * max(n, 1))()
        n = self._ok("get_tuner_gains", buf)
        return [int(buf[i]) for i in range(n)]

    def set_sample_rate(self, hz: int) -> None:
        self._ok("set_sample_rate", int(hz))

    def set_center_freq(self, hz: int) -> None:
        self._ok("set_center_freq", int(hz))

    def set_manual_gain(self, tenth_db: int) -> None:
        """手動の利得。最初の中心周波数の設定より後に呼ぶ（設計Q §6.9 落とし穴①）。"""
        self._ok("set_tuner_gain_mode", 1)
        self._ok("set_tuner_gain", int(tenth_db))

    def set_auto_gain(self) -> None:
        """チューナーの自動利得（設計Q §6.5 の AGC）。"""
        self._ok("set_tuner_gain_mode", 0)

    def set_agc_mode(self, on: bool) -> None:
        self._ok("set_agc_mode", 1 if on else 0)

    def set_ppm(self, ppm: int) -> None:
        """周波数のずれの補正。今と同じ値で −2 が返るのは失敗ではない（設計Q §6.9 落とし穴②）。"""
        r = self._call("set_freq_correction", int(ppm))
        if r < 0 and r != _PPM_UNCHANGED:
            _raise_for("set_freq_correction", r)

    def ppm(self) -> int:
        return self._call("get_freq_correction")

    def reset_buffer(self) -> None:
        self._ok("reset_buffer")

    def read(self, nbytes: int) -> np.ndarray:
        """同期読み。nbytes は READ_BLOCK の倍数。読めたバイト（uint8）を返す（短いことがある）。"""
        if nbytes <= 0 or nbytes % READ_BLOCK:
            raise ValueError(f"読みの大きさは {READ_BLOCK} の倍数（{nbytes}）")
        buf = np.empty(nbytes, dtype=np.uint8)
        n = C.c_int(0)
        r = self._call("read_sync", buf.ctypes.data_as(C.c_void_p), nbytes, C.byref(n))
        if r < 0:
            _raise_for("read_sync", r)
        return buf[: n.value]
