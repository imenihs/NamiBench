"""`rtlsdr.dll` の薄い部品（設計Q §6.2・§6.9）の試験。DLL は読み込まず、探す順と戻り値の扱いを確かめる。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pygigast.core.devices.rtlsdr import dll
from pygigast.core.types import DeviceDisconnected


class TestFind(unittest.TestCase):
    def test_order_configured_app_path(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b, \
                tempfile.TemporaryDirectory() as app:
            for d in (a, b, os.path.join(app, "rtlsdr")):
                os.makedirs(d, exist_ok=True)
                Path(d, dll.DLL_NAME).write_bytes(b"")
            with mock.patch.object(dll, "app_dir", return_value=Path(app)), \
                    mock.patch.dict(os.environ, {"PATH": b}):
                self.assertEqual(dll.find_dll(a), Path(a) / dll.DLL_NAME)
                self.assertEqual(dll.find_dll(None), Path(app) / "rtlsdr" / dll.DLL_NAME)
                os.remove(os.path.join(app, "rtlsdr", dll.DLL_NAME))
                self.assertEqual(dll.find_dll(None), Path(b) / dll.DLL_NAME)

    def test_not_found(self):
        with tempfile.TemporaryDirectory() as empty:
            with mock.patch.object(dll, "app_dir", return_value=Path(empty)), \
                    mock.patch.dict(os.environ, {"PATH": empty}), \
                    mock.patch("ctypes.util.find_library", return_value=None):
                self.assertIsNone(dll.find_dll(empty))
                with self.assertRaises(dll.DllNotFound):
                    dll.RtlSdrLib.load(empty)


class FakeFuncs:
    """RtlSdrHandle が呼ぶ関数の偽物。戻り値を名前ごとに決める。"""

    def __init__(self, returns):
        self._f = {name: (lambda *a, _r=r: _r) for name, r in returns.items()}
        self._f.setdefault("close", lambda *a: 0)


class TestHandle(unittest.TestCase):
    def handle(self, **returns):
        return dll.RtlSdrHandle(FakeFuncs(returns), object())

    def test_ppm_same_value_is_not_error(self):
        self.handle(set_freq_correction=-2).set_ppm(59)
        with self.assertRaises(dll.RtlSdrError):
            self.handle(set_freq_correction=-3).set_ppm(59)

    def test_usb_errors_are_disconnect(self):
        for code in (-1, -4, -5, -7, -9):
            with self.assertRaises(DeviceDisconnected):
                self.handle(set_center_freq=code).set_center_freq(100_000_000)
        with self.assertRaises(dll.RtlSdrError):
            self.handle(set_center_freq=-3).set_center_freq(100_000_000)

    def test_read_size_multiple_of_block(self):
        h = self.handle(read_sync=0)
        for n in (0, 100, dll.READ_BLOCK + 1):
            with self.assertRaises(ValueError):
                h.read(n)
        self.assertEqual(h.read(dll.READ_BLOCK).size, 0)  # 偽物は 0 バイト読んだことにする

    def test_closed(self):
        h = self.handle(reset_buffer=0)
        h.close()
        self.assertFalse(h.is_open)
        with self.assertRaises(DeviceDisconnected):
            h.reset_buffer()


if __name__ == "__main__":
    unittest.main()
