"""ファイルの操作（設計H §4・§4.1・§7.3・I §3.4）。

- 開く（複数・ドラッグ＆ドロップ）→ 参照トレースとして重ねる。保存（既定の名前・前回のフォルダ）
- 書き出し（CSV。範囲は いまの表示／数値で指定）
- PNG の保存・コピー（確認の画面を出さない。保存したら名前を1回知らせ、フォルダを開ける: I §2・§3.4）
- 記録の開始・停止・まとめる。起動時にまとめ終わっていない記録を示して聞く（黙って消さない・変換しない）
- 長い処理の終わり（成功・失敗・やめた）を1回知らせる。失敗・やめたときは何が残っているかを言う
"""
from __future__ import annotations

import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QStandardPaths, Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ..core.units import format_freq
from . import png_export, texts
from .fields import FreqField

if TYPE_CHECKING:
    from .main_window import MainWindow

H5_FILTER = "pyGigaSt の保存ファイル (*.h5);;すべて (*)"
CSV_FILTER = "CSV (*.csv);;すべて (*)"


def default_name(ext: str, when: Optional[datetime.datetime] = None) -> str:
    """既定のファイル名 `gigast_YYYYMMDD_HHMMSS`（H §4）。"""
    return (when or datetime.datetime.now()).strftime("gigast_%Y%m%d_%H%M%S") + ext


def _std_dir(kind) -> Path:
    d = QStandardPaths.writableLocation(kind)
    return Path(d) if d else Path.home()


class FileActions:
    def __init__(self, win: "MainWindow") -> None:
        self.win = win
        self.unfinished_dialog: Optional["UnfinishedDialog"] = None
        self.export_dialog: Optional["ExportDialog"] = None

    # ---------------------------------------------------------------- 場所

    def last_dir(self) -> Path:
        return Path(str(self.win.prefs.get("last_dir", "") or _std_dir(QStandardPaths.StandardLocation.DocumentsLocation)))

    def _remember(self, path) -> None:
        self.win.prefs.set("last_dir", str(Path(path).parent))

    def png_dir(self) -> Path:
        return Path(str(self.win.prefs.get("png_dir", "") or _std_dir(QStandardPaths.StandardLocation.PicturesLocation)))

    # ---------------------------------------------------------------- 開く・保存

    def open_files(self, paths: Optional[list[str]] = None) -> None:
        if paths is None:
            paths = self.win.ask_open_paths("開く", str(self.last_dir()), H5_FILTER)
        paths = [str(p) for p in paths or []]
        if not paths:
            return
        self._remember(paths[0])
        self.win.call("open", paths=paths)

    def save(self) -> None:
        path = self.win.ask_save_path("保存", str(self.last_dir() / default_name(".h5")), H5_FILTER)
        if not path:
            return
        self._remember(path)
        self.win.call("save", path=str(path))

    # ---------------------------------------------------------------- 書き出し（H §7.3）

    def export(self) -> None:
        dlg = ExportDialog(self.win)
        self.export_dialog = dlg
        dlg.accepted.connect(lambda: self._export_chosen(dlg))
        dlg.open()

    def _export_chosen(self, dlg: "ExportDialog") -> None:
        traces, start, stop = dlg.choice()
        path = self.win.ask_save_path("書き出し（CSV）", str(self.last_dir() / default_name(".csv")), CSV_FILTER)
        if not path:
            return
        self._remember(path)
        self.export_csv(path, traces, start, stop)

    def export_csv(self, path: str, traces: list, start_hz: Optional[int], stop_hz: Optional[int]) -> None:
        self.win.call("export_csv", path=str(path), traces=traces, start_hz=start_hz, stop_hz=stop_hz)

    # ---------------------------------------------------------------- PNG（設計I）

    def _png_bytes(self) -> bytes:
        choice = self.win.display_tab.png_palette_choice()
        palette = dict(self.win.pal) if choice == "screen" else choice
        return png_export.render_png(self.win.png_data(), size=self.win.display_tab.png_size_choice(),
                                     palette=palette, note=self.win.display_tab.png_note.text())

    def png_save(self) -> Optional[Path]:
        try:
            d = self.png_dir()
            # save_png はフォルダを渡すと既定の名前で作る。まだ無いフォルダは先に作る
            d.mkdir(parents=True, exist_ok=True)
            out = png_export.save_png(d, self._png_bytes())
        except (OSError, ValueError, RuntimeError) as e:
            self.win.status_note(f"PNG を保存できませんでした: {e}")
            return None
        self.win.status_note(f"保存しました: {out.name}", folder=out.parent)
        return out

    def png_copy(self) -> None:
        try:
            png_export.copy_png_to_clipboard(self._png_bytes())
        except (ValueError, RuntimeError) as e:
            self.win.status_note(f"PNG をコピーできませんでした: {e}")
            return
        self.win.status_note("PNG をクリップボードへコピーしました")

    # ---------------------------------------------------------------- 記録（H §4・§7.2）

    def record_start(self) -> None:
        self.win.call("record_start", on_ok=lambda r: self.win.status_note("記録を始めました"))

    def record_stop(self) -> None:
        self.win.call("record_stop", on_ok=lambda r: self.win.status_note(
            "記録を止めました。「記録をまとめる…」で保存ファイルにします"))

    def record_finish(self) -> None:
        path = self.win.ask_save_path("記録をまとめる", str(self.last_dir() / default_name(".h5")), H5_FILTER)
        if not path:
            return
        self._remember(path)
        self.win.call("record_finish", path=str(path))

    # ---------------------------------------------------------------- 起動時の救出（H §4）

    def check_unfinished(self) -> None:
        def show(r: dict) -> None:
            rows = r.get("recordings") or []
            if not rows:
                return
            dlg = UnfinishedDialog(self, rows)
            self.unfinished_dialog = dlg
            dlg.open()

        self.win.call("list_unfinished", on_ok=show)

    def finish_unfinished(self, work_path: str) -> bool:
        path = self.win.ask_save_path("記録をまとめる", str(self.last_dir() / default_name(".h5")), H5_FILTER)
        if not path:
            return False
        self._remember(path)
        self.win.call("finish_unfinished", path=work_path, h5_path=str(path))
        return True

    def discard_unfinished(self, row: dict) -> bool:
        if not self.win.confirm("記録を捨てる", f"作業ファイルを消します。{row.get('n_sweeps', 0)} 回の掃引の記録が"
                                                 "失われ、戻せません。"):
            return False
        self.win.call("discard_unfinished", path=row["path"],
                      on_ok=lambda r: self.win.status_note("まとめ終わっていない記録を捨てました"))
        return True

    # ---------------------------------------------------------------- 長い処理の終わり（H §4.1）

    def on_task_end(self, code: str, d: dict) -> None:
        kind = d.get("task", "")
        if code == "task_done":
            res = d.get("result") or {}
            text = texts.TASK_DONE.get(kind, kind)
            folder = None
            if res.get("path"):
                p = Path(res["path"])
                text += f": {p.name}"
                folder = p.parent
            if kind == "open":
                files = res.get("files") or []
                text = f"開きました: {len(files)} ファイル"
                errs = res.get("errors") or []
                if errs:
                    text += "・開けなかったもの: " + "、".join(
                        f"{Path(e['path']).name}（{texts.storage_error_text(e)}）" for e in errs)
            elif kind == "export_csv":
                text += f"（{res.get('rows', 0)} 行）"
                warns = [texts.EXPORT_WARNINGS.get(w, w) for w in res.get("warnings") or []]
                if warns:
                    text += "・" + "・".join(warns)
            elif kind == "reference_take":
                if res.get("next") == "short":
                    text = "オープンを測りました。ショートを繋いで「ショートを測る」を押してください"
                else:
                    text = f"基準を取りました: {res.get('name', '')}"
                    self.win.source_tab.refresh()
            self.win.status_note(text, folder=folder)
        elif code == "task_cancelled":
            self.win.status_note(texts.TASK_CANCELLED.get(kind, "やめました"))
        else:
            err = d.get("error") or {}
            text = f"{texts.TASK_FAILED.get(kind, kind)}: {texts.storage_error_text(err)}"
            detail = err.get("detail") or {}
            if detail.get("message"):
                text += f"（{detail['message']}）"
            if kind in ("record_finish", "finish_unfinished"):
                text += "。記録の作業ファイルは残っていて、あとでまとめられます"
            self.win.status_note(text)


class ExportDialog(QDialog):
    """書き出すトレースと周波数の範囲を選ぶ（H §5・§7.3）。"""

    def __init__(self, win: "MainWindow") -> None:
        super().__init__(win)
        self.setWindowTitle("書き出し（CSV）")
        self.win = win
        lay = QVBoxLayout(self)
        g = QGroupBox("トレース")
        v = QVBoxLayout(g)
        self.trace_checks: list[tuple[QCheckBox, object]] = []
        for s in win.state.get("traces") or []:
            if not s.get("has_values"):
                continue
            unit = s.get("unit") or ""
            cb = QCheckBox(f"トレース {s['number']}（{texts.TRACE_ACTIONS.get(s['action'], s['action'])}・{unit}）")
            cb.setChecked(bool(s.get("visible")))
            v.addWidget(cb)
            self.trace_checks.append((cb, int(s["number"])))
        for f in win.state.get("files") or []:
            for t in f.get("traces") or []:
                cb = QCheckBox(f"{f['name']} #{t['number']}（{t['unit']}）")
                v.addWidget(cb)
                self.trace_checks.append((cb, t["id"]))
        if not self.trace_checks:
            v.addWidget(QLabel("書き出せるトレースがありません（掃引するか、ファイルを開いてください）"))
        note = QLabel("周波数の点が違うトレースは同じ表に並べられません")
        note.setObjectName("dim")
        v.addWidget(note)
        lay.addWidget(g)

        g = QGroupBox("周波数の範囲")
        grid = QGridLayout(g)
        sw = win.state.get("sweep") or {}
        self.range_group = QButtonGroup(self)
        self.r_view = QRadioButton(f"いまの表示（{format_freq(sw.get('start_hz', 0))}〜{format_freq(sw.get('stop_hz', 0))}）")
        self.r_custom = QRadioButton("数値で指定")
        self.r_all = QRadioButton("トレースの全部の点")
        self.r_view.setChecked(True)
        for b in (self.r_view, self.r_custom, self.r_all):
            self.range_group.addButton(b)
        self.f_start = FreqField("Start", lambda hz: None, big=False)
        self.f_stop = FreqField("Stop", lambda hz: None, big=False)
        self.f_start.set_hz(int(sw.get("start_hz", 0)))
        self.f_stop.set_hz(int(sw.get("stop_hz", 0)))
        # 確定した値をそのまま欄に残す（この欄はコアへ送らない）
        self.f_start._on_commit = self.f_start.set_hz
        self.f_stop._on_commit = self.f_stop.set_hz
        for f in (self.f_start, self.f_stop):
            f.setEnabled(False)
        self.r_custom.toggled.connect(lambda on: (self.f_start.setEnabled(on), self.f_stop.setEnabled(on)))
        grid.addWidget(self.r_view, 0, 0, 1, 2)
        grid.addWidget(self.r_custom, 1, 0, 1, 2)
        grid.addWidget(self.f_start, 2, 0)
        grid.addWidget(self.f_stop, 2, 1)
        grid.addWidget(self.r_all, 3, 0, 1, 2)
        lay.addWidget(g)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("書き出す…")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(self.trace_checks))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def choice(self) -> tuple[list, Optional[int], Optional[int]]:
        traces = [tid for cb, tid in self.trace_checks if cb.isChecked()]
        if self.r_all.isChecked():
            return traces, None, None
        if self.r_custom.isChecked():
            lo, hi = sorted((self.f_start.hz(), self.f_stop.hz()))
            return traces, lo, hi
        sw = self.win.state.get("sweep") or {}
        return traces, sw.get("start_hz"), sw.get("stop_hz")


class UnfinishedDialog(QDialog):
    """まとめ終わっていない記録（H §4）。何の記録か（開始日時・掃引数・長さ）を示して、まとめるか尋ねる。"""

    def __init__(self, files: FileActions, rows: list[dict]) -> None:
        super().__init__(files.win)
        self.setWindowTitle("まとめ終わっていない記録があります")
        self.files = files
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("前回、保存ファイルへまとめる前に終わった記録です。まとめるか、捨てるかを選んでください。"))
        self.row_widgets: list[QWidget] = []
        for r in rows:
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            started = str(r.get("started_at") or "")
            dur = r.get("duration_s")
            desc = f"{started}  {r.get('n_sweeps', 0)} 回の掃引"
            if dur:
                desc += f"・{dur / 60:.1f} 分"
            if r.get("device_model"):
                desc += f"・{r['device_model']}"
            if r.get("error"):
                desc += f"・読めない所あり（{r['error']}）"
            label = QLabel(desc)
            label.setToolTip(r["path"])
            b_finish = QPushButton("まとめる…")
            b_discard = QPushButton("捨てる…")
            b_finish.setObjectName("small")
            b_discard.setObjectName("small")
            b_finish.clicked.connect(lambda _=False, row=r, ww=w: self._done(ww, files.finish_unfinished(row["path"])))
            b_discard.clicked.connect(lambda _=False, row=r, ww=w: self._done(ww, files.discard_unfinished(row)))
            h.addWidget(label, 1)
            h.addWidget(b_finish)
            h.addWidget(b_discard)
            lay.addWidget(w)
            self.row_widgets.append(w)
        later = QPushButton("あとで（次の起動でもう一度聞く）")
        later.clicked.connect(self.reject)
        lay.addWidget(later, 0, Qt.AlignmentFlag.AlignRight)

    def _done(self, w: QWidget, acted: bool) -> None:
        if acted:
            w.setEnabled(False)
            if all(not x.isEnabled() for x in self.row_widgets):
                self.accept()
