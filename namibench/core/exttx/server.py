"""外部 IQ 送信の口の TCP サーバー（設計S §1 §2）。rtl_tcp の出口（core/rtltcp/server.py）と同じ作り。

- 127.0.0.1 だけで待つ（LAN は範囲の外: 送信を外から動かせる口なので）。相手は1つだけ。2つ目のつなぎには
  error{code:"busy"} を返して閉じる（いまの相手を切って乗っ取らない）
- 受け付けのスレッド1本、相手ごとに受けのスレッドと送りのスレッドを1本ずつ。送りは待ち行列（押すスレッドがクレジットを
  送っても、相手が読まなくても押すスレッドを止めない）
- 機器の状態に触らない。知らせ（つながった・制御のメッセージ・形の誤り・切れた）は on_event で呼び出し側へ渡す
  （呼び出し側は自分の制御スレッドへ渡すこと）。IQ の枠だけは、呼び出し側が渡した sink（押すスレッドの待ち行列）へ
  受けのスレッドから直接渡す（4.096MS/s で 16.4MB/s 流れるので、制御スレッドを通さない）
- 開始の印が "PGIQ" でなければすぐ閉じる（ブラウザの悪意あるページが HTTP を送っても、先頭が "POST" なので何も起きない）
"""
from __future__ import annotations

import collections
import json
import logging
import socket
import threading
from typing import Callable, Optional

from .protocol import (
    HEADER,
    HELLO,
    IQ_MAX_BYTES,
    JSON_MAX_BYTES,
    KIND_IQ,
    KIND_JSON,
    MAGIC,
    PROTOCOL,
    decode_hello,
    encode_json,
)

log = logging.getLogger(__name__)

DEFAULT_PORT = 1235
BIND_ADDR = "127.0.0.1"
# つないでから開始の印が届くまでの上限（秒）。黙ってつないだままの相手で口を塞がない
HELLO_TIMEOUT_S = 5.0
# 最後のメッセージ（stopped・error）を送った後、相手が閉じるのを待つ上限（秒）。読み残しがあるまま閉じると、相手の OS が
# 送った分を捨てることがある（RST）ので、送る側を閉じてから相手の残りを読み捨てて待つ
LINGER_S = 2.0

EventCallback = Callable[[str, int, dict], None]


class _Closed(Exception):
    pass


class _Client:
    def __init__(self, server: "ExtIqServer", cid: int, sock: socket.socket, addr) -> None:
        self.server = server
        self.cid = cid
        self.sock = sock
        self.addr = addr
        self._q: collections.deque = collections.deque()
        self._cond = threading.Condition()
        self._closed = False
        self._close_after = False
        self.sink: Optional[Callable[[bytes], bool]] = None
        self.failed = False
        self._read_done = threading.Event()
        self.greeted = False
        self._sender = threading.Thread(target=self._send_loop, name=f"exttx-send-{cid}", daemon=True)
        self._reader = threading.Thread(target=self._read_loop, name=f"exttx-read-{cid}", daemon=True)

    def start(self) -> None:
        self._sender.start()
        self._reader.start()

    # ---- 送る ----
    def enqueue(self, data: bytes) -> None:
        with self._cond:
            if self._closed or self._close_after:
                return
            self._q.append(data)
            self._cond.notify()

    def close_after_send(self) -> None:
        with self._cond:
            self._close_after = True
            self._cond.notify()

    def _send_loop(self) -> None:
        try:
            while True:
                with self._cond:
                    while not self._q and not self._closed and not self._close_after:
                        self._cond.wait()
                    if self._closed:
                        return
                    if not self._q and self._close_after:
                        break
                    data = self._q.popleft()
                self.sock.sendall(data)
            # 送り終えた: 送る側を閉じ、相手が閉じる（受けのスレッドが読み終える）のを待ってから閉じる
            self.sock.shutdown(socket.SHUT_WR)
            self._read_done.wait(LINGER_S)
        except OSError:
            pass
        finally:
            self.close()

    # ---- 受ける ----
    def _recv_exact(self, n: int) -> bytes:
        buf = bytearray(n)
        view = memoryview(buf)
        got = 0
        while got < n:
            k = self.sock.recv_into(view[got:], n - got)
            if not k:
                raise _Closed()
            got += k
        return bytes(buf)

    def _read_loop(self) -> None:
        srv = self.server
        try:
            self.sock.settimeout(HELLO_TIMEOUT_S)
            magic, major, minor = decode_hello(self._recv_exact(HELLO.size))
            self.sock.settimeout(None)
            if magic != MAGIC:
                return  # 違う印はすぐ閉じる（何も送らない）
            if major != PROTOCOL[0]:
                self.enqueue(encode_json({"type": "error", "code": "unsupported_protocol",
                                          "detail": f"protocol {major}.{minor}", "supported": list(PROTOCOL)}))
                self.close_after_send()
                self._drain()
                return
            self.greeted = True
            self.enqueue(encode_json(srv.welcome()))
            srv._event("connected", self.cid, {"protocol": [major, minor]})
            while True:
                n, kind = HEADER.unpack(self._recv_exact(HEADER.size))
                if self._close_after:
                    self._discard(n)  # 閉じる途中: 残りは読み捨てる
                    continue
                if kind == KIND_JSON:
                    if n > JSON_MAX_BYTES:
                        self._fail("json_too_large")
                        self._discard(n)
                        continue
                    body = self._recv_exact(n)
                    if self.failed:
                        continue
                    try:
                        obj = json.loads(body.decode("utf-8"))
                    except (UnicodeDecodeError, ValueError):
                        self._fail("bad_json")
                        continue
                    if not isinstance(obj, dict) or not isinstance(obj.get("type"), str):
                        self._fail("bad_message")
                        continue
                    srv._event("message", self.cid, obj)
                elif kind == KIND_IQ:
                    if n > IQ_MAX_BYTES or n % 4:
                        self._fail("bad_iq_frame")
                        self._discard(n)
                        continue
                    body = self._recv_exact(n)
                    if self.failed:
                        continue
                    sink = self.sink
                    if sink is None or not sink(body):
                        # ready の前（送ってよい標本の数は 0）か、送ってよい数を超えた
                        self._fail("credit_exceeded")
                else:
                    self._fail("bad_frame_type")
                    self._discard(n)
        except (_Closed, OSError, ValueError):
            pass
        finally:
            self._read_done.set()
            if not self._close_after:
                self.close()  # 閉じる途中なら送りのスレッドが閉じる

    def _drain(self) -> None:
        """相手が閉じるまで読み捨てる（最後のメッセージを相手に届けるため）。"""
        try:
            self.sock.settimeout(LINGER_S)
            while self.sock.recv(65536):
                pass
        except OSError:
            pass

    def _discard(self, n: int) -> None:
        while n > 0:
            k = min(n, 65536)
            self._recv_exact(k)
            n -= k

    def _fail(self, detail: str) -> None:
        if self.failed:
            return
        self.failed = True
        self.sink = None
        self.server._event("protocol_error", self.cid, {"detail": detail})

    def close(self) -> None:
        with self._cond:
            if self._closed:
                return
            self._closed = True
            self._q.clear()
            self._cond.notify_all()
        self.sink = None
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


def _refuse_busy(conn: socket.socket) -> None:
    try:
        conn.sendall(encode_json({"type": "error", "code": "busy", "detail": "another client is connected"}))
        conn.shutdown(socket.SHUT_WR)
        conn.settimeout(LINGER_S)
        while conn.recv(65536):
            pass
    except OSError:
        pass
    finally:
        conn.close()


class ExtIqServer:
    """外部 IQ 送信の口。welcome() は開始の印を受けた直後に送る welcome の中身を返す（受けのスレッドから呼ぶので、
    呼び出し側は制御スレッドが作っておいた辞書を返すだけにする）。"""

    def __init__(self, on_event: EventCallback, welcome: Callable[[], dict]) -> None:
        self.on_event = on_event
        self.welcome = welcome
        self._lock = threading.Lock()
        self._client: Optional[_Client] = None
        self._listen: Optional[socket.socket] = None
        self._accept_thread: Optional[threading.Thread] = None
        self._port: Optional[int] = None
        self._next_id = 0

    @property
    def running(self) -> bool:
        return self._listen is not None

    @property
    def port(self) -> Optional[int]:
        return self._port

    @property
    def client_id(self) -> Optional[int]:
        with self._lock:
            return None if self._client is None else self._client.cid

    def start(self, port: int = DEFAULT_PORT) -> int:
        """127.0.0.1 で待ち受けを始め、実際のポート番号を返す。使えないときは OSError をそのまま上げる。"""
        if self.running:
            raise RuntimeError("already running")
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            s.bind((BIND_ADDR, port))
            s.listen(2)
        except OSError:
            s.close()
            raise
        self._listen = s
        self._port = s.getsockname()[1]
        self._accept_thread = threading.Thread(target=self._accept_loop, args=(s,), name="exttx-accept", daemon=True)
        self._accept_thread.start()
        return self._port

    def stop(self, timeout: float = 2.0) -> None:
        """待ち受けを止め、相手を切る（送ってある分は送らない。送ってから閉じたいときは先に close_client）。"""
        s, self._listen = self._listen, None
        self._port = None
        if s is not None:
            try:
                s.close()
            except OSError:
                pass
        if self._accept_thread is not None:
            self._accept_thread.join(timeout)
            self._accept_thread = None
        with self._lock:
            c = self._client
        if c is not None:
            c.close()
            c.join(timeout)

    def _accept_loop(self, s: socket.socket) -> None:
        while True:
            try:
                conn, addr = s.accept()
            except OSError:
                return
            if self._listen is not s:
                conn.close()
                return
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            with self._lock:
                busy = self._client is not None
                if not busy:
                    self._next_id += 1
                    c = _Client(self, self._next_id, conn, addr)
                    self._client = c
            if busy:
                # いまの相手を切らない。2つ目には busy を返して閉じる（相手の開始の印を読み捨ててから: RST で消さない）
                threading.Thread(target=_refuse_busy, args=(conn,), name="exttx-busy", daemon=True).start()
                continue
            c.start()

    def _remove(self, c: _Client) -> None:
        with self._lock:
            if self._client is not c:
                return
            self._client = None
        if c.greeted:
            self._event("closed", c.cid, {})

    def _event(self, kind: str, cid: int, detail: dict) -> None:
        try:
            self.on_event(kind, cid, detail)
        except Exception:  # noqa: BLE001 — 呼び出し側の不具合で接続の処理を止めない
            log.exception("on_event failed: %s", kind)

    def _current(self, cid: int) -> Optional[_Client]:
        with self._lock:
            c = self._client
        return c if c is not None and c.cid == cid else None

    def send(self, cid: int, obj: dict) -> None:
        """制御のメッセージを送りの待ち行列へ入れる（待たない。どのスレッドから呼んでもよい）。"""
        c = self._current(cid)
        if c is not None:
            c.enqueue(encode_json(obj))

    def close_client(self, cid: int) -> None:
        """待ち行列の分を送り終えてから閉じる（stopped を送ってから閉じる）。"""
        c = self._current(cid)
        if c is not None:
            c.close_after_send()

    def set_sink(self, cid: int, sink: Optional[Callable[[bytes], bool]]) -> None:
        c = self._current(cid)
        if c is not None:
            c.sink = sink
