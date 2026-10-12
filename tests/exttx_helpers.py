"""外部 IQ 送信の口の試験の部品: 偽の PlutoSDR に繋いだ本体と、口に繋ぐ素朴なクライアント（127.0.0.1 だけ）。"""
from __future__ import annotations

import importlib.util
import json
import socket
import struct
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np

from pygigast.core.exttx.protocol import HEADER, KIND_IQ, KIND_JSON, encode_hello, encode_json

ROOT = Path(__file__).resolve().parents[1]
MHZ = 1_000_000
LO = 1_575_420_000
FS = 2_600_000


def load_example_client():
    """tools/ext_iq_client.py（エミュレータが写す例）を読み込む。"""
    spec = importlib.util.spec_from_file_location("ext_iq_client", ROOT / "tools" / "ext_iq_client.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def tone_iq(k: int, n: int, f=250_000, fs=FS, amp=0.3) -> np.ndarray:
    t = np.arange(k, k + n)
    x = amp * np.exp(2j * np.pi * f * t / fs)
    out = np.empty(2 * n, dtype="<i2")
    out[0::2] = np.round(x.real * 32767)
    out[1::2] = np.round(x.imag * 32767)
    return out


class RawClient:
    """口に繋ぐ素朴なクライアント。feed() でトーンを送り続け（credit の中で）、pause・crash・stop を試験から操作する。"""

    def __init__(self, port: int, hello: Optional[bytes] = None) -> None:
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.sock.sendall(encode_hello() if hello is None else hello)
        self.send_lock = threading.Lock()  # 枠を2つのスレッドから混ぜて送らない
        self.msgs: list[dict] = []
        self.cv = threading.Condition()
        self.closed = False
        self.credit = 0
        self.k = 0
        self.paused = threading.Event()
        self.stop_feed = threading.Event()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        self._feeder: Optional[threading.Thread] = None

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            c = self.sock.recv(n - len(buf))
            if not c:
                raise ConnectionError("closed")
            buf += c
        return buf

    def _read(self) -> None:
        try:
            self.sock.settimeout(None)
            while True:
                n, kind = HEADER.unpack(self._recv_exact(HEADER.size))
                m = json.loads(self._recv_exact(n))
                with self.cv:
                    if m.get("type") == "ready":
                        self.credit += m["credit"]
                    elif m.get("type") == "credit":
                        self.credit += m["add"]
                    self.msgs.append(m)
                    self.cv.notify_all()
        except (OSError, ValueError, ConnectionError):
            pass
        finally:
            with self.cv:
                self.closed = True
                self.cv.notify_all()

    def wait(self, pred, timeout=10.0, what="メッセージ"):
        end = time.monotonic() + timeout
        with self.cv:
            while True:
                for m in self.msgs:
                    if pred(m):
                        return m
                left = end - time.monotonic()
                if left <= 0:
                    raise AssertionError(f"{what} が来ない: {self.msgs[-5:]}")
                self.cv.wait(left)

    def wait_type(self, t: str, timeout=10.0) -> dict:
        return self.wait(lambda m: m.get("type") == t, timeout, t)

    def wait_closed(self, timeout=10.0) -> bool:
        end = time.monotonic() + timeout
        with self.cv:
            while not self.closed:
                left = end - time.monotonic()
                if left <= 0:
                    return False
                self.cv.wait(left)
        return True

    def send_json(self, obj: dict) -> None:
        with self.send_lock:
            self.sock.sendall(encode_json(obj))

    def send_raw(self, data: bytes) -> None:
        with self.send_lock:
            self.sock.sendall(data)

    def start(self, rate=FS, lo=LO, name="test") -> dict:
        """start を送り、それに答えた ready か error（送った後に来たもの）を返す。"""
        with self.cv:
            n0 = len(self.msgs)
        self.send_json({"type": "start", "sample_rate_hz": rate, "tx_lo_hz": lo, "client": name})
        end = time.monotonic() + 15
        with self.cv:
            while True:
                for m in self.msgs[n0:]:
                    if m.get("type") in ("ready", "error"):
                        return m
                left = end - time.monotonic()
                if left <= 0:
                    raise AssertionError(f"ready か error が来ない: {self.msgs[-5:]}")
                self.cv.wait(left)

    def feed(self, chunk=8192) -> None:
        def run():
            try:
                while not self.stop_feed.is_set():
                    if self.paused.is_set():
                        time.sleep(0.005)
                        continue
                    with self.cv:
                        if self.closed:
                            return
                        if self.credit < chunk:
                            self.cv.wait(0.05)
                            continue
                        self.credit -= chunk
                    data = tone_iq(self.k, chunk).tobytes()
                    self.k += chunk
                    with self.send_lock:
                        self.sock.sendall(HEADER.pack(len(data), KIND_IQ) + data)
            except OSError:
                pass

        self._feeder = threading.Thread(target=run, daemon=True)
        self._feeder.start()

    def crash(self) -> None:
        """強制終了のまね（stop を送らずにソケットを切る）。"""
        self.stop_feed.set()
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        except OSError:
            pass
        self.sock.close()

    def close(self) -> None:
        self.stop_feed.set()
        try:
            self.sock.close()
        except OSError:
            pass


__all__ = ["FS", "KIND_JSON", "LO", "MHZ", "RawClient", "free_port", "load_example_client", "tone_iq"]
