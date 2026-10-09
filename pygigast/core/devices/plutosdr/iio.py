"""`libiio.dll`（公式 v0.26 の 64 ビット版）を ctypes で直接呼ぶ薄い部品（設計Q §7.2）。

- pylibiio は入れない（依存を増やさない。RTL-SDR の dll.py と同じ考え）。使う関数は文脈（URI）・一覧（scan）・
  機器と通路を探す・属性の読み書き・通路を有効に・バッファを作る・詰める・送る・始まりと終わり・壊すだけ
- DLL はアプリに同梱しない。探す順は ①設定で選んだフォルダ ②アプリのフォルダの `libiio/` ③PATH（設計Q §7.2）
- 64 ビットでない DLL は読み込まない（PE の見出しで判定。設定の画面の DLL の行と同じ: rtlsdr/dll.py の dll_bits）
- 失敗の戻り値（負の errno・NULL）は IioError。読み・書きの通信の失敗は DeviceDisconnected にする（呼ぶ側の判断）
- 同じフォルダの依存 DLL（libxml2・libusb・libserialport・VC の実行時）を見つけられるよう、フォルダを
  DLL の探し場所に足してから読む
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

from ...types import DeviceError
from ..rtlsdr.dll import app_dir, dll_bits

DLL_NAME = "libiio.dll" if sys.platform == "win32" else "libiio.so.0"
APP_SUBDIR = "libiio"


class IioNotFound(DeviceError):
    """libiio の DLL が見つからない・読めない。機器の一覧に PlutoSDR を出さない（GigaSt は使える）。"""


class IioError(DeviceError):
    """libiio の関数が失敗を返した。code は負の errno（分からなければ 0）。"""

    def __init__(self, func: str, code: int = 0, detail: str = "") -> None:
        msg = f"iio_{func} が失敗した（{code}）"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)
        self.func = func
        self.code = code


def candidate_dirs(folder: Optional[Union[str, Path]] = None) -> list[Path]:
    dirs: list[Path] = []
    if folder:
        dirs.append(Path(folder))
    dirs.append(app_dir() / APP_SUBDIR)
    return dirs


def find_dll(folder: Optional[Union[str, Path]] = None) -> Optional[Path]:
    """DLL の場所。見つからなければ None（設計Q §7.2 の順）。"""
    for d in candidate_dirs(folder):
        p = d / DLL_NAME
        if p.is_file():
            return p
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if d and (Path(d) / DLL_NAME).is_file():
            return Path(d) / DLL_NAME
    found = ctypes.util.find_library("iio")
    return Path(found) if found and Path(found).is_file() else None


_p = C.c_void_p

# (名前, 戻り値の型, 引数の型)。名前は iio_ を除いたもの
_SIGNATURES: Sequence[tuple[str, object, list]] = (
    ("library_get_version", None, [C.POINTER(C.c_uint), C.POINTER(C.c_uint), C.c_char_p]),
    ("strerror", None, [C.c_int, C.c_char_p, C.c_size_t]),
    ("create_context_from_uri", _p, [C.c_char_p]),
    ("context_destroy", None, [_p]),
    ("context_set_timeout", C.c_int, [_p, C.c_uint]),
    ("context_get_attr_value", C.c_char_p, [_p, C.c_char_p]),
    ("context_find_device", _p, [_p, C.c_char_p]),
    ("create_scan_context", _p, [C.c_char_p, C.c_uint]),
    ("scan_context_destroy", None, [_p]),
    ("scan_context_get_info_list", C.c_ssize_t, [_p, C.POINTER(C.POINTER(_p))]),
    ("context_info_list_free", None, [C.POINTER(_p)]),
    ("context_info_get_description", C.c_char_p, [_p]),
    ("context_info_get_uri", C.c_char_p, [_p]),
    ("device_find_channel", _p, [_p, C.c_char_p, C.c_bool]),
    ("device_attr_read", C.c_ssize_t, [_p, C.c_char_p, C.c_char_p, C.c_size_t]),
    ("device_attr_write", C.c_ssize_t, [_p, C.c_char_p, C.c_char_p]),
    ("channel_attr_read", C.c_ssize_t, [_p, C.c_char_p, C.c_char_p, C.c_size_t]),
    ("channel_attr_write", C.c_ssize_t, [_p, C.c_char_p, C.c_char_p]),
    ("channel_enable", None, [_p]),
    ("channel_disable", None, [_p]),
    ("device_create_buffer", _p, [_p, C.c_size_t, C.c_bool]),
    ("buffer_destroy", None, [_p]),
    ("buffer_refill", C.c_ssize_t, [_p]),
    ("buffer_push", C.c_ssize_t, [_p]),
    ("buffer_start", _p, [_p]),
    ("buffer_end", _p, [_p]),
)

_loaded: dict[Path, "IioLib"] = {}
_load_lock = threading.Lock()


class IioLib:
    """読み込んだ libiio と、使う関数。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        bits = dll_bits(self.path)
        if sys.platform == "win32" and bits not in (None, 64) and sys.maxsize > 2 ** 32:
            # 64 ビットの Python は 32 ビット版を読めない（SDR# の zip の DLL がこれ: 設計Q §7.2）
            raise IioNotFound(f"64 ビット版の libiio が要ります（{bits} ビット版）")
        if sys.platform == "win32":
            self._dll_dir = os.add_dll_directory(str(self.path.parent))
        try:
            self._lib = C.CDLL(str(self.path))
        except OSError as e:
            raise IioNotFound(f"libiio の DLL を読み込めない: {e}") from e
        self._f = {}
        for name, res, args in _SIGNATURES:
            try:
                f = getattr(self._lib, "iio_" + name)
            except AttributeError as e:
                raise IioNotFound(f"DLL に iio_{name} が無い") from e
            f.restype = res
            f.argtypes = args
            self._f[name] = f

    @classmethod
    def load(cls, folder: Optional[Union[str, Path]] = None) -> "IioLib":
        path = find_dll(folder)
        if path is None:
            raise IioNotFound("libiio の DLL が見つかりません")
        path = path.resolve()
        with _load_lock:
            if path not in _loaded:
                _loaded[path] = cls(path)
            return _loaded[path]

    def version(self) -> tuple[int, int, str]:
        ma, mi = C.c_uint(0), C.c_uint(0)
        tag = C.create_string_buffer(8)
        self._f["library_get_version"](C.byref(ma), C.byref(mi), tag)
        return int(ma.value), int(mi.value), tag.value.decode(errors="replace")

    def strerror(self, code: int) -> str:
        buf = C.create_string_buffer(256)
        self._f["strerror"](abs(int(code)), buf, 256)
        return buf.value.decode(errors="replace")

    def scan(self, backend: str = "usb") -> list[tuple[str, str]]:
        """一覧（(URI, 説明) の並び）。backend は "usb" など。失敗は空の並び（一覧を作れないだけ）。"""
        sc = self._f["create_scan_context"](backend.encode() if backend else None, 0)
        if not sc:
            return []
        try:
            lst = C.POINTER(_p)()
            n = self._f["scan_context_get_info_list"](sc, C.byref(lst))
            if n < 0:
                return []
            try:
                out = []
                for i in range(n):
                    info = lst[i]
                    uri = self._f["context_info_get_uri"](info)
                    desc = self._f["context_info_get_description"](info)
                    out.append(((uri or b"").decode(errors="replace"), (desc or b"").decode(errors="replace")))
                return out
            finally:
                self._f["context_info_list_free"](lst)
        finally:
            self._f["scan_context_destroy"](sc)

    def open(self, uri: str, timeout_ms: int = 3000) -> "IioContext":
        ctx = self._f["create_context_from_uri"](uri.encode())
        if not ctx:
            raise IioError("create_context_from_uri", 0, uri)
        c = IioContext(self, ctx)
        if timeout_ms:
            c.set_timeout(timeout_ms)
        return c


class IioContext:
    """開いた文脈1つ。スレッドをまたいで同時に呼ばない（機器スレッドだけが使う: 設計Q §7.2）。"""

    def __init__(self, lib: IioLib, ctx) -> None:
        self._lib = lib
        self._f = lib._f
        self._ctx = ctx

    @property
    def is_open(self) -> bool:
        return self._ctx is not None

    def _need(self):
        if self._ctx is None:
            raise IioError("context", 0, "閉じている")
        return self._ctx

    def close(self) -> None:
        if self._ctx is not None:
            ctx, self._ctx = self._ctx, None
            self._f["context_destroy"](ctx)

    def set_timeout(self, ms: int) -> None:
        r = self._f["context_set_timeout"](self._need(), int(ms))
        if r < 0:
            raise IioError("context_set_timeout", r, self._lib.strerror(r))

    def attr(self, name: str) -> Optional[str]:
        v = self._f["context_get_attr_value"](self._need(), name.encode())
        return None if v is None else v.decode(errors="replace")

    def device(self, name: str) -> "IioDevice":
        d = self._f["context_find_device"](self._need(), name.encode())
        if not d:
            raise IioError("context_find_device", 0, name)
        return IioDevice(self, d, name)


class IioDevice:
    def __init__(self, ctx: IioContext, dev, name: str) -> None:
        self.ctx = ctx
        self._f = ctx._f
        self._lib = ctx._lib
        self._dev = dev
        self.name = name

    def channel(self, name: str, output: bool) -> "IioChannel":
        ch = self._f["device_find_channel"](self._dev, name.encode(), bool(output))
        if not ch:
            raise IioError("device_find_channel", 0, f"{self.name}/{name}")
        return IioChannel(self, ch, name)

    def attr(self, name: str) -> str:
        buf = C.create_string_buffer(1024)
        r = self._f["device_attr_read"](self._dev, name.encode(), buf, 1024)
        if r < 0:
            raise IioError("device_attr_read", r, f"{self.name}/{name}: {self._lib.strerror(r)}")
        return buf.value.decode(errors="replace")

    def set_attr(self, name: str, value) -> None:
        r = self._f["device_attr_write"](self._dev, name.encode(), str(value).encode())
        if r < 0:
            raise IioError("device_attr_write", r, f"{self.name}/{name}: {self._lib.strerror(r)}")

    def create_buffer(self, samples: int, cyclic: bool = False) -> "IioBuffer":
        b = self._f["device_create_buffer"](self._dev, int(samples), bool(cyclic))
        if not b:
            raise IioError("device_create_buffer", 0, self.name)
        return IioBuffer(self, b, int(samples))


class IioChannel:
    def __init__(self, dev: IioDevice, ch, name: str) -> None:
        self.dev = dev
        self._f = dev._f
        self._lib = dev._lib
        self._ch = ch
        self.name = name

    def attr(self, name: str) -> str:
        buf = C.create_string_buffer(1024)
        r = self._f["channel_attr_read"](self._ch, name.encode(), buf, 1024)
        if r < 0:
            raise IioError("channel_attr_read", r, f"{self.dev.name}/{self.name}/{name}: {self._lib.strerror(r)}")
        return buf.value.decode(errors="replace")

    def set_attr(self, name: str, value) -> None:
        r = self._f["channel_attr_write"](self._ch, name.encode(), str(value).encode())
        if r < 0:
            raise IioError("channel_attr_write", r, f"{self.dev.name}/{self.name}/{name}: {self._lib.strerror(r)}")

    def enable(self) -> None:
        self._f["channel_enable"](self._ch)

    def disable(self) -> None:
        self._f["channel_disable"](self._ch)


class IioBuffer:
    """受信（refill）か送信（push）のバッファ。標本は I・Q の int16 が交互（2 通路を有効にしたとき）。"""

    def __init__(self, dev: IioDevice, buf, samples: int) -> None:
        self.dev = dev
        self._f = dev._f
        self._lib = dev._lib
        self._buf = buf
        self.samples = samples

    def destroy(self) -> None:
        if self._buf is not None:
            b, self._buf = self._buf, None
            self._f["buffer_destroy"](b)

    def _need(self):
        if self._buf is None:
            raise IioError("buffer", 0, "壊してある")
        return self._buf

    def refill(self) -> np.ndarray:
        """詰めて、int16 の並び（I, Q, I, Q …）の写しを返す。"""
        b = self._need()
        r = self._f["buffer_refill"](b)
        if r < 0:
            raise IioError("buffer_refill", r, self._lib.strerror(r))
        s, e = self._f["buffer_start"](b), self._f["buffer_end"](b)
        n = (int(e) - int(s)) // 2 if s and e else 0
        if n <= 0:
            return np.zeros(0, dtype=np.int16)
        return np.ctypeslib.as_array((C.c_int16 * n).from_address(int(s))).copy()

    def push(self, iq: np.ndarray) -> None:
        """int16 の並び（I, Q …）をバッファへ写して送る。長さはバッファの標本数 × 2。"""
        b = self._need()
        s, e = self._f["buffer_start"](b), self._f["buffer_end"](b)
        n = (int(e) - int(s)) // 2 if s and e else 0
        data = np.asarray(iq, dtype=np.int16).ravel()
        if data.size != n:
            raise ValueError(f"送る標本の数が違う（{data.size} ≠ {n}）")
        C.memmove(int(s), data.ctypes.data, n * 2)
        r = self._f["buffer_push"](b)
        if r < 0:
            raise IioError("buffer_push", r, self._lib.strerror(r))
