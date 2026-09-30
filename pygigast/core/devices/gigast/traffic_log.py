"""送受信の記録（設計B §5・§7.5）。既定は OFF。

1回の要求ごとに JSONL へ1行書く: 時刻・送った32バイト（16進）・受け取ったバイト数・
末尾4バイト（生のまま残すだけ。判定には使わない: 設計B §3 の0）・どの上限時間で打ち切ったか。
生の応答は別の .bin に続けて書き、行に位置（raw_offset・raw_len）を入れる。
シミュレータの再生（設計B §7.4）はこの2つのファイルを読む。
"""
from __future__ import annotations

import datetime as _dt
import json
import threading
from pathlib import Path
from typing import Iterator, Optional

TRAILER_LEN = 4


class TrafficLog:
    """送受信の記録。path_base に .jsonl と .bin を付けた2つのファイルへ追記する。"""

    def __init__(self, path_base: Path | str) -> None:
        base = Path(path_base)
        base.parent.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = base.with_suffix(".jsonl")
        self.bin_path = base.with_suffix(".bin")
        self._lock = threading.Lock()
        self._jsonl = open(self.jsonl_path, "a", encoding="utf-8")
        self._bin = open(self.bin_path, "ab")

    def record(
        self,
        request: bytes,
        expected: int,
        response: bytes,
        duration_s: float,
        first_byte_s: Optional[float],
        limit: Optional[str],
        error: Optional[str] = None,
        note: Optional[str] = None,
    ) -> None:
        """1回の要求を記録する。limit は打ち切った上限時間（first_byte・silence・total）か None。"""
        with self._lock:
            offset = self._bin.tell()
            self._bin.write(response)
            self._bin.flush()
            # 末尾4バイトは、ちょうどのバイト数で受け取ったときだけ「末尾」として生のまま残す
            trailer = response[-TRAILER_LEN:].hex() if len(response) == expected and expected >= TRAILER_LEN else None
            row = {
                "time": _dt.datetime.now().astimezone().isoformat(timespec="milliseconds"),
                "request": request.hex(),
                "expected": expected,
                "received": len(response),
                "duration_s": round(duration_s, 4),
                "first_byte_s": None if first_byte_s is None else round(first_byte_s, 4),
                "trailer_raw": trailer,
                "limit": limit,
                "error": error,
                "note": note,
                "raw_file": self.bin_path.name,
                "raw_offset": offset,
                "raw_len": len(response),
            }
            self._jsonl.write(json.dumps(row, ensure_ascii=False) + "\n")
            self._jsonl.flush()

    def close(self) -> None:
        with self._lock:
            self._jsonl.close()
            self._bin.close()


def read_log(jsonl_path: Path | str) -> Iterator[tuple[bytes, bytes, dict]]:
    """記録を読み戻す（要求32バイト, 生の応答, 行）。再生の材料にする。"""
    jsonl_path = Path(jsonl_path)
    with open(jsonl_path, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    blobs: dict[str, bytes] = {}
    for row in rows:
        name = row["raw_file"]
        if name not in blobs:
            blobs[name] = (jsonl_path.parent / name).read_bytes()
        raw = blobs[name][row["raw_offset"]: row["raw_offset"] + row["raw_len"]]
        yield bytes.fromhex(row["request"]), raw, row
