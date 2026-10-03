"""名前を付けて保存する一覧（プリセット: 設計O §1.2・独自の配色: 設計J §4.4.1）。アプリのデータのフォルダに1つ1ファイル。

- 1ファイル1つ（JSON）。中身（content）と一覧に出す要約（summary）は使う側が決める（プリセットは instrument_view.py の
  条件一式、配色は色の組）。校正データとノーマライズの基準はどちらにも入れない
- 書くときは一時ファイルから置き換える（途中で落ちても前の内容が残る）
- 読めないファイルは一覧に出さず、壊れた数として返す（黙って消さない）
- 同じ名前は2つ作らない（呼び出すときにどれか分からなくなるため）
- 失敗のコードは種類の名前を頭に付ける（preset_name_taken・palette_name_taken など。文は画面の表: U-22）

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


def clean_name(name, kind: str = "preset") -> str:
    """前後と続く空白を1つにした名前。空・長すぎるときは {kind}_name_empty・{kind}_name_too_long。"""
    if not isinstance(name, str) or not name.strip():
        raise PresetError(f"{kind}_name_empty")
    n = " ".join(name.split())
    if len(n) > NAME_MAX:
        raise PresetError(f"{kind}_name_too_long", {"max": NAME_MAX})
    return n


class NamedStore:
    """名前の付いた中身の一覧（1つ1ファイル）。kind は失敗のコードの頭（"preset"・"palette"）、fmt はファイルの形の名前。"""

    def __init__(self, folder: Path, kind: str = "preset", fmt: str = FORMAT, version: int = VERSION) -> None:
        self.folder = Path(folder)
        self.kind = kind
        self.fmt = fmt
        self.version = version

    def _err(self, what: str, detail: Optional[dict] = None) -> PresetError:
        # 見つからないものは unknown_{kind}、ほかは {kind}_{what}
        return PresetError(f"unknown_{self.kind}" if what == "unknown" else f"{self.kind}_{what}", detail)

    def _path(self, pid: str) -> Path:
        if not isinstance(pid, str) or not pid.isalnum():
            raise self._err("unknown", {"id": pid})
        return self.folder / f"{pid}.json"

    def parse(self, d) -> Optional[dict]:
        """ファイルの中身（dict）がこの形として読めれば返す。読めなければ None。"""
        if not isinstance(d, dict) or d.get("format") != self.fmt or not isinstance(d.get("version", 0), int) \
                or int(d.get("version", 0)) > self.version or not isinstance(d.get("content"), dict) \
                or not isinstance(d.get("name"), str):
            return None
        return d

    def _read(self, path: Path) -> Optional[dict]:
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return None
        d = self.parse(d)
        if d is None:
            return None
        d["id"] = path.stem
        return d

    def file_text(self, d: dict) -> str:
        """1つ分のファイルの中身（JSON の文字）。保存のファイルと書き出しのファイルは同じ形。"""
        out = {k: v for k, v in d.items() if k != "id"}
        return json.dumps({"format": self.fmt, "version": self.version, **out}, ensure_ascii=False, indent=2)

    def _write(self, d: dict) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self._path(d["id"])
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(self.file_text(d))
        os.replace(tmp, path)

    def list(self, with_content: bool = False) -> tuple[list[dict], int]:
        """(一覧 [{id, name, saved_at, summary}（with_content なら content も）], 読めなかったファイルの数)。
        並びは名前の順。"""
        if not self.folder.is_dir():
            return [], 0
        rows, broken = [], 0
        for p in sorted(self.folder.glob("*.json")):
            d = self._read(p)
            if d is None:
                broken += 1
                continue
            row = {"id": d["id"], "name": d["name"], "saved_at": d.get("saved_at"), "summary": d.get("summary") or {}}
            if with_content:
                row["content"] = d["content"]
            rows.append(row)
        rows.sort(key=lambda r: r["name"])
        return rows, broken

    def get(self, pid: str) -> dict:
        d = self._read(self._path(pid))
        if d is None:
            raise self._err("unknown", {"id": pid})
        return d

    def names(self) -> set[str]:
        return {r["name"] for r in self.list()[0]}

    def _check_unique(self, name: str, except_id: Optional[str] = None) -> None:
        for r in self.list()[0]:
            if r["name"] == name and r["id"] != except_id:
                raise self._err("name_taken", {"name": name})

    def free_name(self, name) -> str:
        """同じ名前があれば「名前 (2)」「名前 (3)」…の空いている名前（取り込むとき: 黙って上書きしない）。"""
        n = clean_name(name, self.kind)
        taken = self.names()
        if n not in taken:
            return n
        i = 2
        while True:
            tail = f" ({i})"
            cand = n[:NAME_MAX - len(tail)] + tail
            if cand not in taken:
                return cand
            i += 1

    def save(self, name, content: dict, summary: Optional[dict] = None, overwrite_id: Optional[str] = None) -> dict:
        """新しく保存する（overwrite_id を渡せばその中身を置き換える）。"""
        n = clean_name(name, self.kind)
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
        n = clean_name(name, self.kind)
        self._check_unique(n, pid)
        d["name"] = n
        self._write(d)
        return {"id": pid, "name": n}

    def delete(self, pid: str) -> None:
        path = self._path(pid)
        if self._read(path) is None:
            raise self._err("unknown", {"id": pid})
        try:
            path.unlink()
        except OSError as e:
            raise self._err("unwritable", {"message": str(e)}) from e


class PresetStore(NamedStore):
    """プリセット（設計O §1.2）の一覧。アプリのデータの presets フォルダ。"""

    def __init__(self, folder: Path) -> None:
        super().__init__(folder, "preset", FORMAT, VERSION)
