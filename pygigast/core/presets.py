"""プリセット（設計O §1.2）: 名前の付いた条件一式を、アプリのデータの presets フォルダに1つ1ファイルで持つ。

- 中身はモード・掃引条件・振幅・トレースの動作と回数・平均の種類・表示の組み合わせ・周波数オフセットなど
  （何を入れるかは本体が決める: instrument_view.py）。校正データとノーマライズの基準は入れない
- 1ファイル1プリセット（JSON）。書くときは一時ファイルから置き換える（途中で落ちても前の内容が残る）
- 読めないファイルは一覧に出さず、壊れた数として返す（黙って消さない）
- 同じ名前は2つ作らない（呼び出すときにどれか分からなくなるため）

Qt を import しない。
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Optional

FORMAT = "pygigast-preset"
VERSION = 1
NAME_MAX = 60


class PresetError(Exception):
    def __init__(self, code: str, detail: Optional[dict] = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail or {}


def _clean_name(name) -> str:
    if not isinstance(name, str) or not name.strip():
        raise PresetError("preset_name_empty")
    n = " ".join(name.split())
    if len(n) > NAME_MAX:
        raise PresetError("preset_name_too_long", {"max": NAME_MAX})
    return n


class PresetStore:
    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)

    def _path(self, pid: str) -> Path:
        if not isinstance(pid, str) or not pid.isalnum():
            raise PresetError("unknown_preset", {"id": pid})
        return self.folder / f"{pid}.json"

    def _read(self, path: Path) -> Optional[dict]:
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return None
        if not isinstance(d, dict) or d.get("format") != FORMAT or int(d.get("version", 0)) > VERSION \
                or not isinstance(d.get("content"), dict) or not isinstance(d.get("name"), str):
            return None
        d["id"] = path.stem
        return d

    def _write(self, d: dict) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self._path(d["id"])
        tmp = path.with_suffix(".tmp")
        out = {k: v for k, v in d.items() if k != "id"}
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"format": FORMAT, "version": VERSION, **out}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    def list(self) -> tuple[list[dict], int]:
        """(一覧 [{id, name, saved_at, summary}], 読めなかったファイルの数)。並びは名前の順。"""
        if not self.folder.is_dir():
            return [], 0
        rows, broken = [], 0
        for p in sorted(self.folder.glob("*.json")):
            d = self._read(p)
            if d is None:
                broken += 1
                continue
            rows.append({"id": d["id"], "name": d["name"], "saved_at": d.get("saved_at"),
                         "summary": d.get("summary") or {}})
        rows.sort(key=lambda r: r["name"])
        return rows, broken

    def get(self, pid: str) -> dict:
        d = self._read(self._path(pid))
        if d is None:
            raise PresetError("unknown_preset", {"id": pid})
        return d

    def _check_unique(self, name: str, except_id: Optional[str] = None) -> None:
        for r in self.list()[0]:
            if r["name"] == name and r["id"] != except_id:
                raise PresetError("preset_name_taken", {"name": name})

    def save(self, name, content: dict, summary: Optional[dict] = None, overwrite_id: Optional[str] = None) -> dict:
        """新しく保存する（overwrite_id を渡せばその中身を置き換える）。"""
        n = _clean_name(name)
        if overwrite_id is not None:
            old = self.get(overwrite_id)
            self._check_unique(n, overwrite_id)
            pid = old["id"]
        else:
            self._check_unique(n)
            pid = uuid.uuid4().hex[:12]
        d: dict[str, Any] = {"id": pid, "name": n, "saved_at": time.time(), "summary": summary or {},
                             "content": content}
        self._write(d)
        return {"id": pid, "name": n, "saved_at": d["saved_at"], "summary": d["summary"]}

    def rename(self, pid: str, name) -> dict:
        d = self.get(pid)
        n = _clean_name(name)
        self._check_unique(n, pid)
        d["name"] = n
        self._write(d)
        return {"id": pid, "name": n}

    def delete(self, pid: str) -> None:
        path = self._path(pid)
        if self._read(path) is None:
            raise PresetError("unknown_preset", {"id": pid})
        try:
            path.unlink()
        except OSError as e:
            raise PresetError("preset_unwritable", {"message": str(e)}) from e
