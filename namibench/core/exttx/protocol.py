"""外部 IQ 送信の口の形（設計S §1 §2.2。すべてリトルエンディアン）。

- 開始の印（クライアント → アプリ、つないで最初の 8 バイト）: "PGIQ" ＋ 版の major（u16）＋ minor（u16）
- 枠（両方向）: 8 バイトの見出し（中身の長さ u32 ＋ 種類 u8 ＋ 0 を 3 バイト）＋ 中身
  - 種類 1 = 制御（UTF-8 の JSON 1つ。type の項目を持つ。64KiB まで）
  - 種類 2 = IQ（クライアント → アプリだけ）。I・Q を交互に並べた int16（ci16le）。長さは 4 の倍数、1MiB まで
"""
from __future__ import annotations

import json
import struct

MAGIC = b"PGIQ"
PROTOCOL = (1, 0)
HELLO = struct.Struct("<4sHH")
HEADER = struct.Struct("<IB3x")
KIND_JSON = 1
KIND_IQ = 2
JSON_MAX_BYTES = 64 * 1024
IQ_MAX_BYTES = 1024 * 1024
FULL_SCALE = 32767


def encode_hello(major: int = PROTOCOL[0], minor: int = PROTOCOL[1], magic: bytes = MAGIC) -> bytes:
    return HELLO.pack(magic, major, minor)


def decode_hello(data: bytes) -> tuple[bytes, int, int]:
    """(印, major, minor)。"""
    magic, major, minor = HELLO.unpack(data)
    return magic, int(major), int(minor)


def encode_frame(kind: int, payload: bytes) -> bytes:
    return HEADER.pack(len(payload), kind) + payload


def encode_json(obj: dict) -> bytes:
    return encode_frame(KIND_JSON, json.dumps(obj, ensure_ascii=False).encode("utf-8"))
