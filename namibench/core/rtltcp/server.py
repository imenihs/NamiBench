"""rtl_tcp の出口の TCP サーバー（設計Q §4.3・§6.7）。

外のソフト（SDR# など）が「rtl_tcp のドングルがつながっている」ものとして使えるように、
IQ を rtl_tcp の形で流し、相手からの命令を受けて呼び出し側へ知らせる。

rtl_tcp の形（一般知識。rtl-sdr の rtl_tcp.c の作りから。SDR# などとつないで確かめる: 設計Q §6.7）
- つながると、先頭の 12 バイトを1回送る: "RTL0"（4バイト）・チューナーの種類（u32 ビッグエンディアン）・
  利得の段数（u32 ビッグエンディアン）。チューナーの種類は 0=不明 1=E4000 2=FC0012 3=FC0013
  4=FC2580 5=R820T 6=R828D
- 以後、I・Q を交互に並べた符号なし 8bit を流し続ける（I0 Q0 I1 Q1 …）。0 の位置は 127.5
- 相手からは 5 バイトの命令が来る: 1 バイトの種類＋4 バイトの値（u32 ビッグエンディアン）
  - 0x01 周波数（Hz）・0x02 サンプリング速度（Hz）・0x03 利得の手動/自動（1=手動）・
    0x04 利得（0.1dB 単位）・0x05 周波数のずれ（ppm。符号付きとして読む）・
    0x06 IF の利得（上位 16bit が段、下位 16bit が利得 0.1dB。符号付き）・0x07 試験の形・
    0x08 AGC・0x09 直接標本化・0x0a オフセットの同調・0x0b RTL の水晶（Hz）・
    0x0c チューナーの水晶（Hz）・0x0d 利得を段の番号で・0x0e バイアスティー
- rtl_tcp 本体は相手を1つしか受けないが、ここは複数を受け、同じ IQ を全員に送る
  （状態バーに接続数を出す: 設計Q §4.3）

スレッドの分け方
- 受け付けのスレッド1本、相手ごとに送りのスレッドと受けのスレッドを1本ずつ
- push_iq は機器スレッドから呼ばれる。相手が遅くても機器スレッドを止めないよう、
  相手ごとの待ち行列はバイト数で上限を持ち、溢れたら古い塊から捨てて数える
- on_command は相手の受けのスレッドから呼ぶ。呼び出し側は自分の制御スレッドへ渡すこと
  （このサーバーは機器の状態に触らない）
"""
from __future__ import annotations

import collections
import logging
import socket
import struct
import threading
from typing import Callable, Optional, Union

import numpy as np

log = logging.getLogger(__name__)

MAGIC = b"RTL0"
DEFAULT_PORT = 1234

# 接続を受ける範囲（設計Q §4.3: 既定はこの PC からだけ。LAN は明示的に選んだときだけ）
BIND_LOCAL = "local"
BIND_LAN = "lan"
_BIND_ADDR = {BIND_LOCAL: "127.0.0.1", BIND_LAN: "0.0.0.0"}

# チューナーの種類（一般知識。rtl-sdr の enum rtlsdr_tuner の値）
TUNER_UNKNOWN = 0
TUNER_E4000 = 1
TUNER_FC0012 = 2
TUNER_FC0013 = 3
TUNER_FC2580 = 4
TUNER_R820T = 5
TUNER_R828D = 6

# 命令の種類と名前（一般知識。rtl_tcp.c の命令の番号）
COMMAND_NAMES: dict[int, str] = {
    0x01: "set_freq",
    0x02: "set_sample_rate",
    0x03: "set_gain_mode",
    0x04: "set_gain",
    0x05: "set_freq_correction",
    0x06: "set_if_gain",
    0x07: "set_test_mode",
    0x08: "set_agc_mode",
    0x09: "set_direct_sampling",
    0x0A: "set_offset_tuning",
    0x0B: "set_rtl_xtal",
    0x0C: "set_tuner_xtal",
    0x0D: "set_gain_by_index",
    0x0E: "set_bias_tee",
}
COMMAND_SIZE = 5
HEADER_SIZE = 12

# 相手ごとの待ち行列の上限（バイト）。2.048MS/s で約 1 秒分（I・Q で 2 バイト/標本）
DEFAULT_MAX_QUEUE_BYTES = 4 * 1024 * 1024

CommandValue = Union[int, tuple[int, int]]
CommandCallback = Callable[[str, CommandValue], None]


def encode_header(tuner_type: int, gain_count: int) -> bytes:
    """先頭の 12 バイト（一般知識: "RTL0"・チューナーの種類・利得の段数。数はビッグエンディアン）。"""
    return MAGIC + struct.pack(">II", tuner_type, gain_count)


def decode_command(data: bytes) -> tuple[str, CommandValue]:
    """5 バイトの命令を (名前, 値) にする。知らない種類は名前 "unknown_0xNN"。

    値は u32 のまま返す。ただし周波数のずれ（0x05）は符号付きの int32、
    IF の利得（0x06）は (段, 利得 0.1dB の符号付き int16) の組にする（一般知識: rtl_tcp.c の読み方）。
    """
    if len(data) != COMMAND_SIZE:
        raise ValueError("command must be 5 bytes")
    cmd = data[0]
    (value,) = struct.unpack(">I", data[1:5])
    name = COMMAND_NAMES.get(cmd, f"unknown_0x{cmd:02x}")
    if cmd == 0x05:
        return name, struct.unpack(">i", data[1:5])[0]
    if cmd == 0x06:
        stage = (value >> 16) & 0xFFFF
        gain = struct.unpack(">h", struct.pack(">H", value & 0xFFFF))[0]
        return name, (stage, gain)
    return name, value


def iq_to_u8(iq: np.ndarray) -> bytes:
    """複素の IQ（I・Q とも振り切れを ±1.0 とする）を、I・Q 交互の符号なし 8bit にする。

    0 は 127.5 の位置（一般知識）。整数に丸めるので 0 は 128 になる（0.5 のずれは受け側で無視できる大きさ）。
    ±1.0 を超えた分は 0 と 255 に切る。
    """
    x = np.asarray(iq, dtype=np.complex64).ravel()
    out = np.empty(x.size * 2, dtype=np.float32)
    out[0::2] = x.real
    out[1::2] = x.imag
    out = np.floor(out * 127.5 + 128.0)
    np.clip(out, 0, 255, out=out)
    return out.astype(np.uint8).tobytes()


class _Client:
    """つながった相手1つ。送りの待ち行列と、送り・受けのスレッドを持つ。"""

    def __init__(self, server: "RtlTcpServer", sock: socket.socket, addr) -> None:
        self.server = server
        self.sock = sock
        self.addr = addr
        self._queue: collections.deque[bytes] = collections.deque()
        self._queued_bytes = 0
        self._cond = threading.Condition()
        self._closed = False
        self.dropped_bytes = 0
        self.dropped_chunks = 0
        # 先頭の 12 バイトを送り終えたか（それまでは待ち行列の先頭を捨てない）
        self.header_sent = False
        self._sender = threading.Thread(target=self._send_loop, name=f"rtltcp-send-{addr}", daemon=True)
        self._reader = threading.Thread(target=self._read_loop, name=f"rtltcp-read-{addr}", daemon=True)

    def start(self, header: bytes) -> None:
        # 先頭の 12 バイトは待ち行列の先頭に置き、捨てる対象にしない（最初に必ず送る）
        with self._cond:
            self._queue.append(header)
            self._queued_bytes += len(header)
        self._sender.start()
        self._reader.start()

    def enqueue(self, chunk: bytes, max_bytes: int, protect_first: bool) -> None:
        with self._cond:
            if self._closed:
                return
            self._queue.append(chunk)
            self._queued_bytes += len(chunk)
            # 溢れたら古い塊から捨てる。先頭の 12 バイトがまだ送られていなければ残す
            keep = 1 if protect_first else 0
            while self._queued_bytes > max_bytes and len(self._queue) > keep + 1:
                old = self._queue[keep]
                del self._queue[keep]
                self._queued_bytes -= len(old)
                self.dropped_bytes += len(old)
                self.dropped_chunks += 1
            self._cond.notify()

    def _send_loop(self) -> None:
        try:
            while True:
                with self._cond:
                    while not self._queue and not self._closed:
                        self._cond.wait()
                    if self._closed:
                        return
                    chunk = self._queue.popleft()
                    self._queued_bytes -= len(chunk)
                    self.header_sent = True
                self.sock.sendall(chunk)
        except OSError:
            pass
        finally:
            self.close()

    def _read_loop(self) -> None:
        buf = b""
        try:
            while True:
                data = self.sock.recv(1024)
                if not data:
                    return
                buf += data
                while len(buf) >= COMMAND_SIZE:
                    raw, buf = buf[:COMMAND_SIZE], buf[COMMAND_SIZE:]
                    self.server._dispatch(raw)
        except OSError:
            pass
        finally:
            self.close()

    def close(self) -> None:
        with self._cond:
            if self._closed:
                return
            self._closed = True
            self._queue.clear()
            self._queued_bytes = 0
            self._cond.notify_all()
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
        self.server._remove(self)

    def join(self, timeout: float) -> None:
        for t in (self._sender, self._reader):
            if t.is_alive() and t is not threading.current_thread():
                t.join(timeout)


class RtlTcpServer:
    """rtl_tcp の出口。start で待ち受け、push_iq で全員へ流し、命令は on_command で知らせる。

    on_command(name, value) は相手の受けのスレッドから呼ぶ。例外は記録して捨てる（接続は切らない）。
    on_clients_changed(count) は接続数が変わったとき（状態バーの更新用: 設計Q §4.3）。
    """

    def __init__(
        self,
        on_command: Optional[CommandCallback] = None,
        on_clients_changed: Optional[Callable[[int], None]] = None,
        tuner_type: int = TUNER_UNKNOWN,
        gain_count: int = 0,
        max_queue_bytes: int = DEFAULT_MAX_QUEUE_BYTES,
    ) -> None:
        self.on_command = on_command
        self.on_clients_changed = on_clients_changed
        self.tuner_type = tuner_type
        self.gain_count = gain_count
        self.max_queue_bytes = max_queue_bytes
        self._lock = threading.Lock()
        self._clients: list[_Client] = []
        self._listen: Optional[socket.socket] = None
        self._accept_thread: Optional[threading.Thread] = None
        self._port: Optional[int] = None
        self._bind: Optional[str] = None
        # いまの IQ のサンプリング速度（push_iq で受けた値。外の命令の値ではない）
        self.sample_rate: Optional[int] = None
        # 接続が切れた相手の分も含めた、捨てた量の合計
        self._dropped_bytes_closed = 0
        self._dropped_chunks_closed = 0

    # ---- 状態 ----

    @property
    def running(self) -> bool:
        return self._listen is not None

    @property
    def port(self) -> Optional[int]:
        """待ち受けているポート（port=0 で始めたときは OS が選んだ番号）。止めているときは None。"""
        return self._port

    @property
    def bind(self) -> Optional[str]:
        return self._bind

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    @property
    def dropped_bytes(self) -> int:
        with self._lock:
            return self._dropped_bytes_closed + sum(c.dropped_bytes for c in self._clients)

    @property
    def dropped_chunks(self) -> int:
        with self._lock:
            return self._dropped_chunks_closed + sum(c.dropped_chunks for c in self._clients)

    def set_header(self, tuner_type: int, gain_count: int) -> None:
        """次につながる相手に送る先頭の中身を変える（機器を替えたとき）。つながっている相手には送り直さない。"""
        self.tuner_type = tuner_type
        self.gain_count = gain_count

    # ---- 開始・停止 ----

    def start(self, port: int = DEFAULT_PORT, bind: str = BIND_LOCAL) -> int:
        """待ち受けを始め、実際のポート番号を返す。ポートが使えないときは OSError をそのまま上げる。"""
        if bind not in _BIND_ADDR:
            raise ValueError(f"bind must be {BIND_LOCAL!r} or {BIND_LAN!r}")
        if self.running:
            raise RuntimeError("already running")
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            # Windows で他のプロセスと同じポートを共有しないよう、排他で開く
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            s.bind((_BIND_ADDR[bind], port))
            s.listen(4)
        except OSError:
            s.close()
            raise
        self._listen = s
        self._port = s.getsockname()[1]
        self._bind = bind
        self._accept_thread = threading.Thread(target=self._accept_loop, args=(s,), name="rtltcp-accept", daemon=True)
        self._accept_thread.start()
        return self._port

    def stop(self, timeout: float = 2.0) -> None:
        """待ち受けを止め、全員を切る。"""
        s = self._listen
        self._listen = None
        self._port = None
        self._bind = None
        if s is not None:
            try:
                s.close()
            except OSError:
                pass
        if self._accept_thread is not None:
            self._accept_thread.join(timeout)
            self._accept_thread = None
        with self._lock:
            clients = list(self._clients)
        for c in clients:
            c.close()
        for c in clients:
            c.join(timeout)

    def _accept_loop(self, s: socket.socket) -> None:
        while True:
            try:
                conn, addr = s.accept()
            except OSError:
                return  # stop で閉じた
            if self._listen is not s:
                conn.close()
                return
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            c = _Client(self, conn, addr)
            with self._lock:
                self._clients.append(c)
                n = len(self._clients)
            c.start(encode_header(self.tuner_type, self.gain_count))
            self._notify_clients(n)

    def _remove(self, c: _Client) -> None:
        with self._lock:
            if c not in self._clients:
                return
            self._clients.remove(c)
            self._dropped_bytes_closed += c.dropped_bytes
            self._dropped_chunks_closed += c.dropped_chunks
            n = len(self._clients)
        self._notify_clients(n)

    def _notify_clients(self, n: int) -> None:
        cb = self.on_clients_changed
        if cb is None:
            return
        try:
            cb(n)
        except Exception:  # 画面側の不具合で接続の処理を止めない
            log.exception("on_clients_changed failed")

    def _dispatch(self, raw: bytes) -> None:
        name, value = decode_command(raw)
        cb = self.on_command
        if cb is None:
            return
        try:
            cb(name, value)
        except Exception:  # 呼び出し側の不具合で接続を切らない
            log.exception("on_command failed: %s", name)

    # ---- 送る ----

    def push_iq(self, iq: np.ndarray, sample_rate: int) -> None:
        """IQ の塊を全員の待ち行列へ入れる。止まっていない・相手がいないときは何もしない。

        iq は complex64 で、I・Q とも振り切れを ±1.0 とする。機器スレッドから呼んでよい（待たない）。
        """
        self.sample_rate = int(sample_rate)
        with self._lock:
            clients = list(self._clients)
        if not clients:
            return
        chunk = iq_to_u8(iq)
        if not chunk:
            return
        for c in clients:
            c.enqueue(chunk, self.max_queue_bytes, protect_first=not c.header_sent)
