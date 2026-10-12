"""pyGigaSt の外部 IQ 送信の口へ IQ を流す最小のクライアント（プロトコル 1.0）。標準ライブラリと numpy だけ。

pyGigaSt の「設定 → 外部のアプリから送信」で受け付けを入れ、PlutoSDR に繋いでスペアナのモードにしてから動かす。
送信はケーブルと減衰器で被測定物へつなぎ、空中に出さない。

    python ext_iq_client.py --tone 250000 --rate 2600000 --lo 1575420000 --seconds 30

エミュレータに写すときは run() の make_iq（標本の番号 k から k+n を作る関数）を差し替える。流れの決まり:
- つないだら最初に開始の印（"PGIQ"・版 1.0）を送り、welcome を受ける
- start（標本化の速さ・送信の LO・名前）を送り、ready を待つ。断られたら error（code と理由）が来る。
  標本化の速さは 1kHz の倍数にすると、スペアナの RBW 375〜1500Hz（Hann）がいつも使える
- 送ってよい標本の数（credit）を超えて送らない。アプリは塊を DAC に渡すたびに credit で数を返す（エミュレータの時間が
  DAC の時計に縛られる）。超えて送ると protocol_error で止まる
- 1.0 秒続けて送らないと（starved）、アプリは送信を止めて閉じる
- 終えるときは stop を送る。アプリは送信を止め、stopped を送って閉じる
"""
from __future__ import annotations

import argparse
import json
import socket
import struct
import threading
from typing import Callable, Optional

import numpy as np

MAGIC, PROTO = b"PGIQ", (1, 0)
T_JSON, T_IQ = 1, 2
HDR = struct.Struct("<IB3x")  # 中身の長さ・種類・0 を3バイト


def send(sock, kind, payload: bytes):
    sock.sendall(HDR.pack(len(payload), kind) + payload)


def send_json(sock, obj):
    send(sock, T_JSON, json.dumps(obj).encode())


def recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed by pyGigaSt")
        buf += chunk
    return bytes(buf)


def recv_json(sock):
    n, kind = HDR.unpack(recv_exact(sock, HDR.size))
    body = recv_exact(sock, n)
    if kind != T_JSON:
        raise ValueError(f"unexpected frame type {kind}")
    return json.loads(body)


def tone(fs: int, tone_hz: float, amplitude: float = 0.3) -> Callable[[int, int, int], np.ndarray]:
    """通しの標本番号 k から n 標本の複素のトーン（振り切れ 1.0）を作る関数（位相は塊をまたいでつながる）。"""

    def make(k: int, n: int, full: int) -> np.ndarray:
        t = np.arange(k, k + n)
        return amplitude * np.exp(2j * np.pi * tone_hz * t / fs)

    return make


def to_ci16(x: np.ndarray, full: int) -> bytes:
    iq = np.empty(2 * x.size, dtype="<i2")
    iq[0::2] = np.round(x.real * full)
    iq[1::2] = np.round(x.imag * full)
    return iq.tobytes()


def run(fs=2_600_000, lo_hz=1_575_420_000, tone_hz=250_000, seconds=30.0, port=1235, host="127.0.0.1",
        name="example", make_iq: Optional[Callable[[int, int, int], np.ndarray]] = None, chunk=16384,
        log=print) -> dict:
    """流して、終わりの知らせ（stopped か closed）を返す。"""
    s = socket.create_connection((host, port))
    try:
        s.sendall(MAGIC + struct.pack("<HH", *PROTO))
        hello = recv_json(s)
        if hello["type"] != "welcome":
            return hello  # busy・unsupported_protocol
        send_json(s, {"type": "start", "sample_rate_hz": int(fs), "tx_lo_hz": int(lo_hz), "client": name})
        ready = recv_json(s)
        if ready["type"] != "ready":
            return ready  # error: 理由のコードと範囲など
        log("ready:", ready)
        full = hello["full_scale"]
        make_iq = make_iq or tone(fs, tone_hz)
        state = {"credit": ready["credit"], "end": None, "on": None}
        cv = threading.Condition()

        def reader():  # credit を足し、stopped か切断で終わる
            try:
                while True:
                    m = recv_json(s)
                    with cv:
                        if m["type"] == "credit":
                            state["credit"] += m["add"]
                        elif m["type"] == "on":
                            state["on"] = m
                        elif m["type"] == "stopped":
                            state["end"] = m
                        cv.notify_all()
                    if m["type"] == "stopped":
                        return
            except (ConnectionError, OSError, ValueError) as e:
                with cv:
                    state["end"] = state["end"] or {"type": "closed", "error": repr(e)}
                    cv.notify_all()

        threading.Thread(target=reader, daemon=True).start()
        k, total = 0, int(fs * seconds)
        while k < total:
            with cv:
                while state["credit"] < chunk and state["end"] is None:
                    cv.wait()
                if state["end"] is not None:
                    break
                state["credit"] -= chunk
            send(s, T_IQ, to_ci16(make_iq(k, chunk, full), full))
            k += chunk
        if state["end"] is None:
            send_json(s, {"type": "stop"})
            with cv:
                cv.wait_for(lambda: state["end"] is not None, timeout=3.0)
        return state["end"] or {"type": "closed"}
    finally:
        s.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="pyGigaSt の外部 IQ 送信の口へトーンを流す")
    ap.add_argument("--tone", type=float, default=250_000, help="LO からのトーンの離れ（Hz）")
    ap.add_argument("--rate", type=int, default=2_600_000, help="標本化の速さ（Hz）")
    ap.add_argument("--lo", type=int, default=1_575_420_000, help="送信の LO（Hz）")
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--port", type=int, default=1235)
    a = ap.parse_args()
    print("end:", run(a.rate, a.lo, a.tone, a.seconds, a.port))


if __name__ == "__main__":
    main()
