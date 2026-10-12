"""状態バー（J §2・M-04: 動作中の機能を常に出す）。項目と語は texts.status_items（Web と同じ並び・同じ文）。

- rtl_tcp を出している間はその項目の隣に「止める」を置く（設計Q §4.3: 状態バーからすぐ止められる）
- 聞いている間はその項目の隣に「やめる」を置く（設計Q §6.8）
- 外部 IQ 送信の口に相手がつながっている間は、その項目の隣に「送信を止める」を置く（設計S §1 §7）
- 一度きりの知らせ（U-05）は右に出し、保存したものならそこから保存先を開ける（U-11・I §3.4）
- 状態バーの文字とボタンは小の段（高さ 20px・文字 12px: J §4.11・S-01）
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QLabel, QPushButton, QSizePolicy

from . import sizes, texts

if TYPE_CHECKING:
    from .main_window import MainWindow

KEYS = ("conn", "run", "view", "time", "discard", "cal", "ext", "offset", "norm", "src", "listen", "rtltcp", "exttx", "rec",
        "tasks")


class StatusBar:
    def __init__(self, win: "MainWindow") -> None:
        self.win = win
        sb = win.statusBar()
        self.labels: dict[str, QLabel] = {}
        for key in KEYS:
            lab = QLabel()
            sizes.set_tier(lab, "S")
            # 項目と項目の間はまとまりの間（S-04）。部品の間（6px）だと隣の項目と続けて読めてしまう。
            # 余白は文字の頭に取る（見た目の決まりが欄の外の余白を決め直すので、QLabel の字下げで取る）
            if key != KEYS[0]:
                lab.setIndent(sizes.SECTION_GAP)
            self.labels[key] = lab
            sb.addWidget(lab)
            if key == "listen":
                self.listen_stop = self._small(texts.LISTEN_LABELS["stop"], lambda: win.listen.stop())
                sb.addWidget(self.listen_stop)
            elif key == "rtltcp":
                self.rtltcp_stop = self._small(texts.SETTINGS_LABELS["rtltcp_stop"],
                                               lambda: win.call("rtltcp_set", enabled=False))
                sb.addWidget(self.rtltcp_stop)
            elif key == "exttx":
                self.exttx_stop = self._small(texts.EXT_TX_LABELS["stop"], lambda: win.call("ext_tx_stop"))
                sb.addWidget(self.exttx_stop)
        # Web の口を LAN に開いている間の接続の数。押すと相手の一覧（設計A §4.3.2 の 5）
        self.web = self._small("", lambda: win.web.show_clients())
        sb.addWidget(self.web)
        self.note = QLabel()
        sizes.set_tier(self.note, "S")
        self.note.setIndent(sizes.SECTION_GAP)
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
        sizes.set_tier(b, "S")  # 状態バーの部品は小の段（J §4.11・S-01）
        b.clicked.connect(fn)
        b.hide()
        return b

    def apply(self, st: dict) -> None:
        items = {i["key"]: i for i in texts.status_items(st)}
        for key, lab in self.labels.items():
            it = items.get(key)
            lab.setText(it["text"] if it else "")
            lab.setToolTip((it.get("tip") or it["text"]) if it else "")
            lab.setVisible(it is not None)
            name = "warn" if it and it.get("cls") == "warn" else ""
            if lab.objectName() != name:
                lab.setObjectName(name)
                lab.style().unpolish(lab)
                lab.style().polish(lab)
        rt = st.get("rtltcp") or {}
        self.rtltcp_stop.setVisible(bool(rt.get("running") or (rt.get("enabled") and rt.get("error"))))
        self.listen_stop.setVisible(bool((st.get("listen") or {}).get("active")))
        self.exttx_stop.setVisible(bool(items.get("exttx", {}).get("stop")))

    def show_note(self, text: str, folder: Optional[Path] = None) -> None:
        self.note.setText(f"{time.strftime('%H:%M:%S')} {text}")
        self.note.setToolTip(text)
        self._folder = Path(folder) if folder else None
        self.note_button.setVisible(folder is not None)

    def _open_folder(self) -> None:
        if self._folder is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._folder)))
