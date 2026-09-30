"""コアの設定ファイル（設計O §1.1）。

覚えた機器・いまの状態（掃引条件など）・配色・rtl_tcp の送出の設定を1つの JSON に持つ。
- 版を持つ
- 読めない・壊れているときは初期値で始め、壊れたファイルは別名で残し、何を初期値にしたかを返す（黙って消さない）
- 書くときは一時ファイルから置き換える（途中で落ちても前の内容が残る）
"""
from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path
from typing import Any, Optional

FORMAT = "pygigast-settings"
VERSION = 1

DEFAULTS: dict[str, Any] = {
    "known_devices": {"serials": [], "last": None},
    "state": {},
    "theme": {},
    "rtltcp": {"enabled": False, "port": 1234, "bind": "local"},
}


class Settings:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        # 読み込みで初期値に戻したもの（画面へ1回知らせる）。None は問題なし
        self.load_problem: Optional[dict] = None
        self._load()

    def _load(self) -> None:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return
        except OSError as e:
            self.load_problem = {"reason": "unreadable", "message": str(e)}
            return
        try:
            d = json.loads(raw.decode("utf-8"))
            if not isinstance(d, dict) or d.get("format") != FORMAT:
                raise ValueError("format")
            if int(d.get("version", 0)) > VERSION:
                raise ValueError("newer_version")
        except (ValueError, UnicodeDecodeError) as e:
            kept = self.path.with_name(self.path.stem + time.strftime(".broken-%Y%m%d-%H%M%S") + self.path.suffix)
            try:
                os.replace(self.path, kept)
            except OSError:
                kept = None
            self.load_problem = {"reason": "broken", "message": str(e), "kept_as": str(kept) if kept else None}
            return
        for key, default in DEFAULTS.items():
            v = d.get(key)
            if isinstance(v, type(default)):
                if isinstance(default, dict):
                    merged = copy.deepcopy(default)
                    merged.update(v)
                    self.data[key] = merged
                else:
                    self.data[key] = v

    def get(self, key: str) -> Any:
        return copy.deepcopy(self.data.get(key))

    def set(self, key: str, value: Any) -> None:
        self.data[key] = copy.deepcopy(value)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        out = {"format": FORMAT, "version": VERSION, **self.data}
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    # 覚えた機器（設計B §7.3）
    def known_serials(self) -> list[str]:
        return list(self.data["known_devices"].get("serials", []))

    def last_serial(self) -> Optional[str]:
        return self.data["known_devices"].get("last")

    def remember_device(self, serial: str) -> None:
        kd = self.data["known_devices"]
        if serial not in kd["serials"]:
            kd["serials"].append(serial)
        kd["last"] = serial
        self.save()
