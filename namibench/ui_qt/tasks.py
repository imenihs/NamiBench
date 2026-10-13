"""長い処理の進み具合（設計H §4.1・U-19）。

- 動いている処理ごとに1行: 何をしているか・何件目/全体・経過時間・「やめる」
- 1秒未満で終わる処理には何も出さない（U-19 の「〜1秒」）。1秒を超えたら出す
- 終わりの知らせ（成功・失敗・やめた）は画面側が状態バーに出す
- 時間は約束しない（残り時間を出さない: U-20）
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFrame, QLabel, QProgressBar, QPushButton, QWidget

from . import sizes, texts

if TYPE_CHECKING:
    from .main_window import MainWindow

SHOW_AFTER_S = 1.0
BAR_MAX_W = 260


class _Row(QWidget):
    def __init__(self, panel: "TaskPanel", task_id: str) -> None:
        super().__init__()
        self.task_id = task_id
        lay = sizes.hbox(self)
        self.label = QLabel("")
        self.bar = QProgressBar()
        # 進み具合の帯は横に伸ばしすぎない（行の文字を押し出さない）
        self.bar.setMaximumWidth(BAR_MAX_W)
        self.cancel = QPushButton(texts.LABELS["cancel"])
        self.cancel.clicked.connect(lambda: panel.win.call("cancel_task", task_id=task_id, quiet_codes=("unknown_task",)))
        lay.addWidget(self.label, 1)
        lay.addWidget(self.bar)
        lay.addWidget(self.cancel)

    def show_task(self, t: dict) -> None:
        self.label.setText(texts.task_label(t))
        total = int(t.get("total") or 0)
        if total > 0:
            self.bar.setRange(0, total)
            self.bar.setValue(min(int(t.get("done") or 0), total))
        else:
            self.bar.setRange(0, 0)  # 全体が分からない間は動いていることだけを示す
        self.cancel.setEnabled(not t.get("cancelling"))


class TaskPanel(QFrame):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        self.setObjectName("taskPanel")
        self.tasks: dict[str, dict] = {}
        self._rows: dict[str, _Row] = {}
        self._lay = sizes.vbox(self)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)

    def apply_tasks(self, tasks: list[dict]) -> None:
        now = {t["task_id"]: t for t in tasks or []}
        for tid in list(self.tasks):
            if tid not in now:
                self.tasks.pop(tid)
                row = self._rows.pop(tid, None)
                if row is not None:
                    row.deleteLater()
        for tid, t in now.items():
            base = self.tasks.get(tid, {})
            merged = {**base, **t, "task": t.get("kind")}
            # 状態の done/total より新しい進み具合が届いていれば、そちらを残す
            if base.get("done", 0) > t.get("done", 0):
                merged["done"] = base["done"]
            self.tasks[tid] = merged
        self._tick()

    def on_progress(self, p: dict) -> None:
        tid = p.get("task_id")
        if tid is None:
            return
        t = self.tasks.setdefault(tid, {"task_id": tid, "kind": p.get("task"), "started_at": time.time()})
        t.update({"task": p.get("task"), "done": p.get("done", 0), "total": p.get("total", 0),
                  "elapsed_s": p.get("elapsed_s", 0.0)})
        self._tick()

    def _tick(self) -> None:
        shown = 0
        for tid, t in self.tasks.items():
            started = t.get("started_at") or time.time()
            t["elapsed_s"] = max(float(t.get("elapsed_s") or 0.0), time.time() - started)
            if t["elapsed_s"] < SHOW_AFTER_S:
                continue
            row = self._rows.get(tid)
            if row is None:
                row = _Row(self, tid)
                self._rows[tid] = row
                self._lay.addWidget(row)
            row.show_task(t)
            shown += 1
        self.setVisible(shown > 0)
        if self.tasks and not self._timer.isActive():
            self._timer.start()
        elif not self.tasks:
            self._timer.stop()

    def count(self) -> int:
        return len(self.tasks)
