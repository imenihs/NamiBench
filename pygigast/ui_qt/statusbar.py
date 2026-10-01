"""状態バー（J §2・M-04: 動作中の機能を常に出す）。項目と語は texts.status_items（Web と同じ並び・同じ文）。

- rtl_tcp を出している間はその項目の隣に「止める」を置く（設計Q §4.3: 状態バーからすぐ止められる）
- 聞いている間はその項目の隣に「やめる」を置く（設計Q §6.8）
- 一度きりの知らせ（U-05）は右に出し、保存したものならそこから保存先を開ける（U-11・I §3.4）
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QLabel, QPushButton, QSizePolicy

from . import texts

if TYPE_CHECKING:
    from .main_window import MainWindow

KEYS = ("conn", "run", "time", "discard", "cal", "ext", "offset", "norm", "src", "listen", "rtltcp", "rec", "tasks")


class StatusBar:
    def __init__(self, win: "MainWindow") -> None:
        self.win = win
        sb = win.statusBar()
        self.labels: dict[str, QLabel] = {}
        for key in KEYS:
            lab = QLabel()
            self.labels[key] = lab
            sb.addWidget(lab)
            if key == "listen":
                self.listen_stop = self._small(texts.LISTEN_LABELS["stop"], lambda: win.listen.stop())
                sb.addWidget(self.listen_stop)
            elif key == "rtltcp":
                self.rtltcp_stop = self._small(texts.SETTINGS_LABELS["rtltcp_stop"],
                                               lambda: win.call("rtltcp_set", enabled=False))
                sb.addWidget(self.rtltcp_stop)
        # Web の口を LAN に開いている間の接続の数。押すと相手の一覧（設計A §4.3.2 の 5）
        self.web = self._small("", lambda: win.web.show_clients())
        sb.addWidget(self.web)
        self.note = QLabel()
        # 一度きりの知らせは幅が足りないとき先に縮める（状態の項目を切らない。全文はツールチップ）
        self.note.setMinimumWidth(1)
        self.note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.note_button = self._small(texts.LABELS["open_folder"], self._open_folder)
        sb.addWidget(self.note, 1)
        sb.addWidget(self.note_button)
        self._folder: Optional[Path] = None

    @staticmethod
    def _small(text: str, fn) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName("small")
        b.clicked.connect(fn)
        b.hide()
        return b

    def apply(self, st: dict) -> None:
        items = {i["key"]: i for i in texts.status_items(st)}
        for key, lab in self.labels.items():
            it = items.get(key)
            lab.setText(it["text"] if it else "")
            lab.setToolTip(it["text"] if it else "")
            lab.setVisible(it is not None)
            name = "warn" if it and it.get("cls") == "warn" else ""
            if lab.objectName() != name:
                lab.setObjectName(name)
                lab.style().unpolish(lab)
                lab.style().polish(lab)
        rt = st.get("rtltcp") or {}
        self.rtltcp_stop.setVisible(bool(rt.get("running") or (rt.get("enabled") and rt.get("error"))))
        self.listen_stop.setVisible(bool((st.get("listen") or {}).get("active")))

    def show_note(self, text: str, folder: Optional[Path] = None) -> None:
        self.note.setText(f"{time.strftime('%H:%M:%S')} {text}")
        self.note.setToolTip(text)
        self._folder = Path(folder) if folder else None
        self.note_button.setVisible(folder is not None)

    def _open_folder(self) -> None:
        if self._folder is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._folder)))
