"""機器の切断の記録（設計B §7.7）: 接続・切断・復帰の出来事を、利用者のデータの場所の logs/device_events.jsonl へ
1行ずつ残す。いつも書く（設定は無い）。送受信の記録（設計B §7.5。生バイトを書くシミュレータの材料）とは別のもの。

- 1行 = 1つの出来事。共通の欄は time（タイムゾーン付き）・event・reason・kind（機器の種類）・model・serial。
  出来事ごとの中身は detail に入れる
- 1 MB を超える行を書く前に .1〜.4 へ回す（いちばん古い .4 は消す。全部で最大 5 ファイル・5 MB）
- 書けなくても測定は止めない: 失敗は数えて（failures）、最初の1回だけ標準エラーに出す。画面には出さない
  （設計B §7.7 は画面に足さない。記録が書けないことは測定の結果を変えないので、知らせで測定の画面を邪魔しない）
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import threading
from pathlib import Path
from typing import Optional

LOG_DIR = "logs"
LOG_NAME = "device_events.jsonl"
MAX_BYTES = 1_000_000  # 1つのファイルの上限（バイト）
BACKUPS = 4  # 回した古いファイルの数（.1〜.4）


def now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="milliseconds")


def iso(t: Optional[float]) -> Optional[str]:
    """時刻（time.time()）をタイムゾーン付きの文字列に。None はそのまま。"""
    if t is None:
        return None
    return datetime.datetime.fromtimestamp(float(t)).astimezone().isoformat(timespec="milliseconds")


def make_record(event: str, reason: Optional[str], kind: Optional[str], model: Optional[str],
                serial: Optional[str], detail: Optional[dict] = None) -> dict:
    return {"time": now_iso(), "event": event, "reason": reason, "kind": kind, "model": model, "serial": serial,
            "detail": detail or {}}


def describe_error(exc: BaseException) -> dict:
    """切れた理由の例外から残す欄: 例外の種類・文・元の例外の種類の並び・libiio の関数名・errno と strerror（取れるとき）。

    libiio の失敗（func と code を持つ例外）が原因の並びにあればそこから、無ければ errno を持つ OSError（シリアルポート
    など）から取る。strerror が例外に無ければ errno から作る。"""
    chain: list[BaseException] = []
    e: Optional[BaseException] = exc
    while e is not None and e not in chain:
        chain.append(e)
        e = e.__cause__ or e.__context__
    out: dict = {"exception": type(exc).__name__, "message": str(exc)}
    if len(chain) > 1:
        out["cause_chain"] = [type(c).__name__ for c in chain[1:]]
    errno: Optional[int] = None
    strerror: Optional[str] = None
    iio = next((c for c in chain if hasattr(c, "func") and hasattr(c, "code")), None)
    if iio is not None:
        out["iio_function"] = f"iio_{iio.func}"
        errno = getattr(iio, "errno", None) or (abs(int(iio.code or 0)) or None)
        strerror = getattr(iio, "strerror", None)
    else:
        ose = next((c for c in chain if isinstance(c, OSError) and c.errno), None)
        if ose is not None:
            errno, strerror = int(ose.errno), ose.strerror
    if errno and not strerror:
        try:
            strerror = os.strerror(int(errno))
        except (ValueError, OverflowError):
            strerror = None
    out["errno"] = errno
    out["strerror"] = strerror
    return out


class DeviceEventLog:
    """回しながら追記する JSONL。どのスレッドから書いてもよい。"""

    def __init__(self, path: Path, max_bytes: int = MAX_BYTES, backups: int = BACKUPS) -> None:
        self.path = Path(path)
        self.max_bytes = int(max_bytes)
        self.backups = int(backups)
        self.failures = 0
        self.last_failure: Optional[str] = None
        self._lock = threading.Lock()

    def _backup(self, i: int) -> Path:
        return self.path.with_name(f"{self.path.name}.{i}")

    def _rotate(self) -> None:
        last = self._backup(self.backups)
        if last.exists():
            last.unlink()
        for i in range(self.backups - 1, 0, -1):
            src = self._backup(i)
            if src.exists():
                os.replace(src, self._backup(i + 1))
        os.replace(self.path, self._backup(1))

    def write(self, record: dict) -> bool:
        """1行書く。書けたら True。失敗は数えて False（例外は外へ出さない）。"""
        with self._lock:
            try:
                line = (json.dumps(record, ensure_ascii=False, default=str) + "\n").encode("utf-8")
                self.path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    size = self.path.stat().st_size
                except FileNotFoundError:
                    size = 0
                if size and size + len(line) > self.max_bytes:
                    self._rotate()
                with open(self.path, "ab") as f:
                    f.write(line)
                return True
            except Exception as e:  # noqa: BLE001 — 記録の失敗で測定を止めない
                self.failures += 1
                self.last_failure = f"{type(e).__name__}: {e}"
                if self.failures == 1:
                    print(f"機器の切断の記録を書けない（以後は数えるだけ）: {self.last_failure}", file=sys.stderr)
                return False
