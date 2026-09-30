"""この画面だけの覚え書き（設計O §1.1: 窓の分割比・前回の保存先フォルダ・PNG の配色などは画面ごと）。

アプリでは QSettings（利用者ごとの保存場所）に置く。試験では何も残さないよう、辞書だけで持つ。
"""
from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import QSettings


class UiPrefs:
    def __init__(self, settings: Optional[QSettings] = None) -> None:
        self._s = settings
        self._mem: dict[str, Any] = {}

    @classmethod
    def for_app(cls) -> "UiPrefs":
        return cls(QSettings("pyGigaSt", "pyGigaSt-qt"))

    def get(self, key: str, default: Any = None) -> Any:
        if self._s is None:
            return self._mem.get(key, default)
        v = self._s.value(key, default)
        return default if v is None else v

    def set(self, key: str, value: Any) -> None:
        if self._s is None:
            self._mem[key] = value
        else:
            self._s.setValue(key, value)
