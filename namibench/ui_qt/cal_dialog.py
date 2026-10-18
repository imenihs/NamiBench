"""校正の画面（設計K §4.1〜§4.5。メニュー「校正」の項目から開く）。

手順を案内する別の窓（M-10）: 準備 → 測る（旧ファイルは読み込む）→ 確かめる → 保存。補正値はコアが計算する
（利用者に足し引きさせない: K §2）。作業の値はコアの状態 `calibration_work` にあり、中身の文字・行・使えない理由は
cal_view.view（Web の app_cal.js と同じ）が作る。ここは映して、押されたら命令を送るだけ。
- 窓の題は開いたメニューの項目の名前（U-24）。何を校正するかを準備で変えると題も変わる
- 測った値があるのに閉じる・切り替えるときは、何が失われるかを言って聞く。測った点だけ保存することも選べる（U-18）
- 測るのは数秒〜数十秒（PlutoSDR の利得の補正は数分）。進み具合とやめるボタンを出し、終わり方（測れなかった理由・
  やめたこと）をその手順のそばに出す（U-19・U-25）
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QGridLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import cal_view, sizes, texts
from .fields import DbField, FreqField
from .texts_cal import CAL_TEXTS as C

if TYPE_CHECKING:
    from .main_window import MainWindow

PREPARE, MEASURE, CHECK, SAVED = range(4)
# 測るの手順の頁の並び（コアの校正の種類の並びと同じ）
KINDS = ("freq", "rbw", "nf", "pluto_gain", "legacy")
_RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
_LEFT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
# 進み具合の帯は横に伸ばしすぎない（main の tasks.py と同じ）
BAR_MAX_W = 240


class _Diagram(QWidget):
    """つなぎ方の図（K §4.1 の 1）: 箱を左から右へ矢印でつなぐ。中身は cal_view の diagram（Web と同じ）。"""

    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        self.blocks: list[str] = []

    def set_blocks(self, blocks: list[str]) -> None:
        if blocks != self.blocks:
            self.blocks = list(blocks)
            self.updateGeometry()
            self.update()

    def _metrics(self):
        fm = self.fontMetrics()
        pad, arrow = sizes.BTN_PAD_X, 28
        widths = [fm.horizontalAdvance(b) + 2 * pad for b in self.blocks]
        return widths, arrow

    def sizeHint(self) -> QSize:  # noqa: N802 — Qt の名前
        widths, arrow = self._metrics()
        return QSize(sum(widths) + arrow * max(0, len(widths) - 1) + 2, sizes.H_M + 2)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def paintEvent(self, _e) -> None:  # noqa: N802
        if not self.blocks:
            return
        pal = self.win.pal
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        widths, arrow = self._metrics()
        x, h = 1.0, float(sizes.H_M)
        for i, (b, bw) in enumerate(zip(self.blocks, widths)):
            r = QRectF(x, 1.0, bw, h)
            p.setPen(QPen(QColor(pal["accent"]), 1))
            p.setBrush(QColor(pal["graph_bg"]))
            p.drawRoundedRect(r, 3, 3)
            p.setPen(QColor(pal["text"]))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, b)
            x += bw
            if i < len(self.blocks) - 1:
                y = 1.0 + h / 2
                p.setPen(QPen(QColor(pal["text_dim"]), 1.5))
                p.drawLine(QPointF(x + 4, y), QPointF(x + arrow - 6, y))
                p.setBrush(QColor(pal["text_dim"]))
                p.drawPolygon(QPolygonF([QPointF(x + arrow - 4, y), QPointF(x + arrow - 10, y - 4),
                                         QPointF(x + arrow - 10, y + 4)]))
                x += arrow
        p.end()


def _table(columns: int, readonly: bool = False) -> QTableWidget:
    """表。readonly は読むだけ（行を選ばない・フォーカスを取らない）。"""
    t = QTableWidget(0, columns)
    t.verticalHeader().hide()
    if readonly:
        t.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        t.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    if not readonly:
        t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    t.horizontalHeader().setStretchLastSection(True)
    return t


def _fit_columns(t: QTableWidget) -> None:
    """表の幅を、全部の列が切れずに入る幅以上にする（列が増えた一覧が狭い窓で見切れないように）。"""
    hh = t.horizontalHeader()
    n = t.columnCount()
    need = sum(max(t.sizeHintForColumn(c), hh.sectionSizeHint(c)) for c in range(n))
    if n and hh.stretchLastSection():
        # 伸ばす最後の列は、狭くても既定の幅（defaultSectionSize）より縮まない
        c = n - 1
        need += max(0, hh.defaultSectionSize() - max(t.sizeHintForColumn(c), hh.sectionSizeHint(c)))
    t.setMinimumWidth(need + t.verticalScrollBar().sizeHint().width() + 2 * t.frameWidth())


def _set_row(t: QTableWidget, row: int, cells: list[str], right: tuple[int, ...], warn_cols: tuple[int, ...] = (),
             warn_color: Optional[str] = None, tips: Optional[dict] = None, bold: bool = False) -> None:
    for c, text in enumerate(cells):
        it = t.item(row, c)
        if it is None:
            it = QTableWidgetItem()
            t.setItem(row, c, it)
        it.setText(text)
        f = QFont(t.font())
        f.setBold(bold)
        it.setFont(f)
        it.setTextAlignment(_RIGHT if c in right else _LEFT)
        it.setForeground(QColor(warn_color) if (warn_color and c in warn_cols) else t.palette().text().color())
        it.setToolTip((tips or {}).get(c, ""))


class CalDialog(QDialog):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__(win)
        self.win = win
        self.v: Optional[dict] = None
        self.page = PREPARE
        self._asked_pending: Optional[int] = None
        self._closing = False
        self._points_sig = None
        self._check_sig = None
        lay = sizes.vbox(self, inset=False)
        sizes.window(lay)
        # 手順の並び（いまの手順を太字で）
        self.step_labels = [QLabel("") for _ in range(4)]
        steps = sizes.hbox()
        for i, lab in enumerate(self.step_labels):
            if i:
                arrow = QLabel("›")
                arrow.setObjectName("dim")
                steps.addWidget(arrow)
            steps.addWidget(lab)
        steps.addStretch(1)
        lay.addLayout(steps)
        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_prepare())
        self.pages.addWidget(self._build_measure())
        self.pages.addWidget(self._build_check())
        self.pages.addWidget(self._build_saved())
        lay.addWidget(self.pages, 1)
        self.btn_back = QPushButton(C["back"])
        self.btn_back.clicked.connect(self._back)
        self.btn_next = QPushButton(C["next"])
        self.btn_next.clicked.connect(self._next)
        self.btn_close = QPushButton(C["close"])
        self.btn_close.clicked.connect(self.close)
        row = sizes.hbox()
        row.addWidget(self.btn_back)
        row.addWidget(self.btn_next)
        row.addStretch(1)
        row.addWidget(self.btn_close)
        lay.addLayout(row)
        for b in self.findChildren(QPushButton):
            b.setAutoDefault(False)
            b.setDefault(False)
        # 主の窓より少し小さく（決めた解像度の画面に収まる大きさ: C-01）
        self.resize(min(1100, max(760, win.width() - 80)), min(820, max(560, win.height() - 80)))

    # ------------------------------------------------------------------ 組む

    def _build_prepare(self) -> QWidget:
        w = QWidget()
        f = sizes.form(w)
        self.device = QLabel("")
        self.device_reason = QLabel("")
        self.device_reason.setObjectName("warn")
        dev = sizes.hbox()
        dev.addWidget(self.device)
        dev.addWidget(self.device_reason)
        dev.addStretch(1)
        f.addRow(C["device"], dev)
        self.base = QLabel("")
        f.addRow(C["current_cal"], self.base)
        self.kind_group = QButtonGroup(self)
        kinds = QVBoxLayout()
        kinds.setContentsMargins(0, 0, 0, 0)
        kinds.setSpacing(0)
        self.kind_radios: dict[str, QRadioButton] = {}
        for i, code in enumerate(KINDS):
            r = QRadioButton(cal_view.kind_label(code))
            self.kind_group.addButton(r, i)
            self.kind_radios[code] = r
            r.clicked.connect(lambda _c=False, k=code: self._choose_kind(k))
            kinds.addWidget(r)
        f.addRow(C["kind_group"], kinds)
        self.need = QLabel("")
        self.need.setWordWrap(True)
        # 欄の列の幅いっぱいを使う（折り返す文の大きさの見込みは狭く、そのままでは 2 行目が行の高さで切れた）
        self.need.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        f.addRow(C["need"], self.need)
        self.wiring_name = QLabel(C["wiring"])
        wiring = QVBoxLayout()
        wiring.setContentsMargins(0, 0, 0, 0)
        wiring.setSpacing(sizes.GAP)
        self.diagram = _Diagram(self.win)
        self.wiring = QLabel("")
        wiring.addWidget(self.diagram, 0, Qt.AlignmentFlag.AlignLeft)
        wiring.addWidget(self.wiring)
        self.wiring_box = QWidget()
        self.wiring_box.setLayout(wiring)
        f.addRow(self.wiring_name, self.wiring_box)
        return w

    def _progress_row(self):
        box = QWidget()
        h = sizes.hbox(box)
        bar = QProgressBar()
        bar.setMaximumWidth(BAR_MAX_W)
        bar.setTextVisible(False)
        label = QLabel("")
        cancel = QPushButton(texts.LABELS["abort"])
        cancel.clicked.connect(self._cancel_task)
        h.addWidget(bar)
        h.addWidget(label)
        h.addWidget(cancel)
        h.addStretch(1)
        box.bar, box.label, box.cancel = bar, label, cancel
        box.hide()
        return box

    def _message_label(self) -> QLabel:
        m = QLabel("")
        m.setObjectName("warn")
        m.setWordWrap(True)
        m.hide()
        return m

    def _build_measure(self) -> QWidget:
        self.measure_pages = QStackedWidget()
        # ---- 周波数特性（K §4.2）: 点の一覧と、いまの点の指示
        w = QWidget()
        h = sizes.hbox(w)
        self.points = _table(6)
        self.points.itemSelectionChanged.connect(self._point_selected)
        self.points.setMinimumWidth(330)
        h.addWidget(self.points, 2)
        right = sizes.vbox(inset=False)
        f = sizes.form()
        self.f_sg = DbField(C["sg_level"], "dBm", lambda v: self._set(sg_level_dbm=v, field=self.f_sg), big=False)
        self.f_loss = DbField(C["cable_loss"], "dB", lambda v: self._set(cable_loss_db=v, field=self.f_loss),
                              big=False)
        f.addRow(self.f_sg.name, self.f_sg)
        f.addRow(self.f_loss.name, self.f_loss)
        right.addLayout(f)
        self.instruction = QLabel("")
        fi = QFont(self.instruction.font())
        fi.setBold(True)
        self.instruction.setFont(fi)
        right.addWidget(self.instruction)
        self.btn_measure = QPushButton(C["measure"])
        self.btn_measure.clicked.connect(lambda: self._measure())
        self.btn_skip = QPushButton(C["skip"])
        self.btn_skip.clicked.connect(lambda: self.win.call("cal_skip", on_ok=self._ok))
        right.addLayout(sizes.buttons_row(self.btn_measure, self.btn_skip))
        self.freq_progress = self._progress_row()
        right.addWidget(self.freq_progress)
        self.freq_message = self._message_label()
        right.addWidget(self.freq_message)
        right.addStretch(1)
        h.addLayout(right, 3)
        self.measure_pages.addWidget(w)
        # ---- RBW（K §4.3）: 全自動
        w = QWidget()
        v = sizes.vbox(w, inset=False)
        self.rbw_extra = QCheckBox(C["rbw_extra"])
        self.rbw_extra.clicked.connect(lambda on: self._set(rbw_extra=bool(on)))
        v.addWidget(self.rbw_extra)
        self.btn_rbw = QPushButton(C["start"])
        self.btn_rbw.clicked.connect(lambda: self._measure())
        v.addLayout(sizes.buttons_row(self.btn_rbw))
        self.rbw_progress = self._progress_row()
        v.addWidget(self.rbw_progress)
        self.rbw_message = self._message_label()
        v.addWidget(self.rbw_message)
        self.rbw_table = _table(6, readonly=True)
        self.rbw_table.horizontalHeader().setStretchLastSection(False)
        v.addWidget(self.rbw_table, 1)
        self.measure_pages.addWidget(w)
        # ---- NF の増幅度（K §4.4）: アッテネータあり → なしの2段
        w = QWidget()
        v = sizes.vbox(w, inset=False)
        f = sizes.form()
        self.f_nf = FreqField(C["nf_freq"], lambda hz: self._set(nf_freq_hz=int(hz), field=self.f_nf), big=False)
        f.addRow(self.f_nf.name, self.f_nf)
        v.addLayout(f)
        g = QGridLayout()
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(sizes.LABEL_GAP)
        g.setVerticalSpacing(sizes.GAP)
        self.nf_rows = []
        for i, state in enumerate(("with_att", "without_att")):
            lab = QLabel("")
            btn = QPushButton(C["measure"])
            btn.clicked.connect(lambda _c=False, s=state: self._measure(s))
            val = QLabel("")
            g.addWidget(lab, i, 0)
            g.addWidget(btn, i, 1)
            g.addWidget(val, i, 2)
            self.nf_rows.append((lab, btn, val))
        g.setColumnStretch(3, 1)
        v.addLayout(g)
        self.nf_result = QLabel("")
        v.addWidget(self.nf_result)
        self.nf_progress = self._progress_row()
        v.addWidget(self.nf_progress)
        self.nf_message = self._message_label()
        v.addWidget(self.nf_message)
        v.addStretch(1)
        self.measure_pages.addWidget(w)
        # ---- PlutoSDR の利得の補正（設計Q §7.6）: 全自動。周波数ごとの結果の一覧と、減衰器の値・始める
        w = QWidget()
        h = sizes.hbox(w)
        self.pluto_table = _table(5, readonly=True)
        self.pluto_table.setMinimumWidth(330)
        h.addWidget(self.pluto_table, 3)
        right = sizes.vbox(inset=False)
        f = sizes.form()
        self.f_ext = DbField(C["ext_atten"], "dB", lambda v: self._set(ext_atten_db=v, field=self.f_ext), big=False)
        f.addRow(self.f_ext.name, self.f_ext)
        right.addLayout(f)
        self.btn_pluto = QPushButton(C["start"])
        self.btn_pluto.clicked.connect(lambda: self._measure())
        right.addLayout(sizes.buttons_row(self.btn_pluto))
        self.pluto_progress = self._progress_row()
        right.addWidget(self.pluto_progress)
        self.pluto_message = self._message_label()
        right.addWidget(self.pluto_message)
        right.addStretch(1)
        h.addLayout(right, 2)
        self.measure_pages.addWidget(w)
        # ---- 旧校正ファイル（K §4.5・設計D §5.6）
        w = QWidget()
        v = sizes.vbox(w, inset=False)
        f = sizes.form()
        self.legacy_file = QLabel("")
        self.btn_file = QPushButton(C["choose_file"])
        self.btn_file.clicked.connect(self._choose_file)
        row = sizes.hbox()
        row.addWidget(self.legacy_file)
        row.addWidget(self.btn_file)
        row.addStretch(1)
        f.addRow(C["file"], row)
        v.addLayout(f)
        self.legacy_message = self._message_label()
        v.addWidget(self.legacy_message)
        v.addStretch(1)
        self.measure_pages.addWidget(w)
        return self.measure_pages

    def _build_check(self) -> QWidget:
        w = QWidget()
        v = sizes.vbox(w, inset=False)
        self.items = _table(6, readonly=True)
        v.addWidget(self.items)
        h = sizes.vbox(inset=False)
        self.graph = pg.PlotWidget()
        self.graph.setMouseEnabled(x=False, y=False)
        self.graph.hideButtons()
        self.graph.setMenuEnabled(False)
        self.curve_before = self.graph.plot([], [])
        self.curve_after = self.graph.plot([], [])
        self.marks = pg.ScatterPlotItem(size=9, symbol="o")
        self.graph.addItem(self.marks)
        self.legend = self.graph.addLegend(offset=(10, 10))
        h.addWidget(self.graph, 2)
        self.check_points = _table(6, readonly=True)
        h.addWidget(self.check_points, 3)
        self.check_area = QWidget()
        self.check_area.setLayout(h)
        v.addWidget(self.check_area, 1)
        # 周波数特性の無い手順（RBW・NF）は表だけ。表を上に寄せる
        self.check_filler = QWidget()
        v.addWidget(self.check_filler, 1)
        self.save_reason = QLabel("")
        self.save_reason.setObjectName("dim")
        v.addWidget(self.save_reason)
        return w

    def _build_saved(self) -> QWidget:
        w = QWidget()
        v = sizes.vbox(w, inset=False)
        self.saved = QLabel("")
        self.saved.setWordWrap(True)
        v.addWidget(self.saved)
        v.addStretch(1)
        return w

    # ------------------------------------------------------------------ 状態を映す

    def apply_state(self, st: dict) -> None:
        v = cal_view.view(st)
        if v is None:
            # 作業が閉じた（別の画面で閉じた）。聞くことは無い
            if self.isVisible() and not self._closing:
                self._closing = True
                self.close()
                self._closing = False
            self.v = None
            return
        kind_changed = self.v is not None and self.v["kind"] != v["kind"]
        self.v = v
        if kind_changed and self.page in (MEASURE, CHECK):
            self.page = PREPARE
        self._render()

    def on_progress(self, p: dict) -> None:
        if self.v is None or p.get("task_id") != self.v.get("task_id"):
            return
        box = self._progress_box()
        if box is not None:
            total = int(p.get("total") or 0)
            box.bar.setRange(0, max(total, 1))
            box.bar.setValue(min(int(p.get("done") or 0), max(total, 1)))
            box.label.setText(texts.task_label(p))

    def _progress_box(self):
        kind = (self.v or {}).get("kind")
        return {"freq": self.freq_progress, "rbw": self.rbw_progress, "nf": self.nf_progress,
                "pluto_gain": self.pluto_progress}.get(kind)

    def _render(self) -> None:
        v = self.v
        self.setWindowTitle(v["title"])
        steps_key = (tuple(v["steps"]), self.page)
        if getattr(self, "_steps_key", None) != steps_key:  # 状態は掃引ごとに届くので、変わったときだけ描き直す
            self._steps_key = steps_key
            for i, lab in enumerate(self.step_labels):
                lab.setText(v["steps"][i])
                f = QFont(lab.font())
                f.setBold(i == self.page)
                lab.setFont(f)
                lab.setObjectName("" if i == self.page else "dim")
                lab.style().unpolish(lab)
                lab.style().polish(lab)
        self.pages.setCurrentIndex(self.page)
        # ---- 準備
        self.device.setText(v["device"])
        self.device_reason.setText(v["device_reason"] or "")
        self.device_reason.setVisible(bool(v["device_reason"]))
        self.base.setText(v["base"])
        for code, r in self.kind_radios.items():
            r.setChecked(code == v["kind"])
            r.setEnabled(not v["measuring"])
        self.need.setText(v["need"])
        self.wiring.setText(v["wiring"])
        self.diagram.set_blocks(v["diagram"])
        self.wiring_name.setVisible(bool(v["wiring"]))
        self.wiring_box.setVisible(bool(v["wiring"]))
        # ---- 測る
        self.measure_pages.setCurrentIndex(KINDS.index(v["kind"]))
        self._render_measure(v)
        # ---- 確かめる・保存
        self._render_check(v)
        self.saved.setText(v["saved_text"])
        self._render_buttons(v)
        if v.get("pending") and self._asked_pending != v["pending"]["index"] and self.isVisible():
            self._asked_pending = v["pending"]["index"]
            QTimer.singleShot(0, self._ask_pending)
        elif not v.get("pending"):
            self._asked_pending = None

    def _render_progress(self, box, v: dict) -> None:
        box.setVisible(v["measuring"])
        p = v.get("progress")
        if p:
            box.bar.setRange(0, max(int(p["total"]), 1))
            box.bar.setValue(min(int(p["done"]), max(int(p["total"]), 1)))
            box.label.setText(p["text"])

    def _render_message(self, label: QLabel, v: dict) -> None:
        label.setText(v["message"] or "")
        label.setVisible(bool(v["message"]))

    def _render_measure(self, v: dict) -> None:
        kind = v["kind"]
        if kind == "freq":
            h = v["headers"]
            # band の境目は2行（どちらの帯の欄か・SG に出させる周波数: K §4.2.1）
            self._fill_points(self.points, v["points"],
                              [h["freq"], h["band"], h["sg_freq"], h["before"], h["after"], h["state"]],
                              lambda p: [p["freq"], p["band"], p["sg_freq"], p["before"], p["after"],
                                         (C["big_mark"] + " " if p["big"] else "") + p["state"]],
                              right=(0, 2, 3, 4), select=True)
            _fit_columns(self.points)
            self.f_sg.set_value(v["inputs"]["sg_level_dbm"])
            self.f_loss.set_value(v["inputs"]["cable_loss_db"])
            for f in (self.f_sg, self.f_loss):
                f.edit.setEnabled(not v["measuring"])
            self.instruction.setText(v.get("instruction") or "")
            self._enable(self.btn_measure, v.get("measure_reason"))
            self._enable(self.btn_skip, v.get("skip_reason"))
            self._render_progress(self.freq_progress, v)
            self._render_message(self.freq_message, v)
        elif kind == "rbw":
            self.rbw_extra.setChecked(v["inputs"]["rbw_extra"])
            self.rbw_extra.setEnabled(not v["measuring"])
            self._enable(self.btn_rbw, v.get("measure_reason"))
            heads = v.get("rbw_headers") or []
            self.rbw_table.setColumnCount(len(heads))
            self.rbw_table.setHorizontalHeaderLabels(heads)
            for i, hd in enumerate(heads):
                self.rbw_table.horizontalHeaderItem(i).setTextAlignment(_RIGHT)
            rows = v.get("rbw_rows") or []
            self.rbw_table.setRowCount(len(rows))
            for r, row in enumerate(rows):
                # 補正の値に使う周波数（500MHz）の行は太字（ほかの周波数は確かめる用: K §4.3）
                _set_row(self.rbw_table, r, row["cells"], tuple(range(len(row["cells"]))), bold=row["used"])
            self._render_progress(self.rbw_progress, v)
            self._render_message(self.rbw_message, v)
        elif kind == "nf":
            self.f_nf.set_hz(v["inputs"]["nf_freq_hz"])
            self.f_nf.edit.setEnabled(not v["measuring"])
            for (lab, btn, val), row in zip(self.nf_rows, v.get("nf_rows") or []):
                lab.setText(row["text"])
                val.setText(row["value"])
                self._enable(btn, v.get("measure_reason"))
            self.nf_result.setText(v.get("nf_result") or "")
            self._render_progress(self.nf_progress, v)
            self._render_message(self.nf_message, v)
        elif kind == "pluto_gain":
            self.f_ext.set_value(v["inputs"]["ext_atten_db"])
            self.f_ext.edit.setEnabled(not v["measuring"])
            self._enable(self.btn_pluto, v.get("measure_reason"))
            self._fill_pluto(v)
            self._render_progress(self.pluto_progress, v)
            self._render_message(self.pluto_message, v)
        else:
            self.legacy_file.setText(v.get("legacy_file") or "")
            self._enable(self.btn_file, v.get("measure_reason"))
            self._render_message(self.legacy_message, v)

    def _fill_pluto(self, v: dict) -> None:
        """周波数ごとの結果（測った周波数は利得の範囲・送信の減衰・補正の範囲）。変わったときだけ描き直す。"""
        t = self.pluto_table
        heads = v.get("pluto_headers") or []
        rows = v.get("pluto_rows") or []
        sig = (tuple(heads), tuple(tuple(r["cells"]) for r in rows))
        if getattr(t, "_sig", None) == sig:
            return
        t._sig = sig
        t.setColumnCount(len(heads))
        t.setHorizontalHeaderLabels(heads)
        right = tuple(range(len(heads) - 1))
        for i in range(len(heads)):
            t.horizontalHeaderItem(i).setTextAlignment(_RIGHT if i in right else _LEFT)
        t.setRowCount(len(rows))
        for r, row in enumerate(rows):
            _set_row(t, r, row["cells"], right)

    def _fill_points(self, t: QTableWidget, points: list, headers: list, cells, right: tuple[int, ...],
                     select: bool = False, warn: bool = False) -> None:
        sig = (id(t), tuple((p["freq"], p["band"], p["sg_freq"], p["before"], p["after"], p["state"], p["big"],
                             p.get("warn"),
                             p["current"] if select else False) for p in points), tuple(headers))
        if getattr(t, "_sig", None) == sig:
            return
        t._sig = sig
        t.blockSignals(True)
        t.setColumnCount(len(headers))
        t.setHorizontalHeaderLabels(headers)
        for i in range(len(headers)):
            t.horizontalHeaderItem(i).setTextAlignment(_RIGHT if i in right else _LEFT)
        t.setRowCount(len(points))
        warn_color = self.win.pal.get("warn")
        for r, p in enumerate(points):
            row = cells(p)
            tips = {len(row) - 1: p["warn"]} if warn and p.get("warn") else ({len(row) - 1: self.v["big_tip"]}
                                                                             if p["big"] else {})
            _set_row(t, r, row, right, warn_cols=(len(row) - 1,) if (p["big"] or p.get("warn")) else (),
                     warn_color=warn_color, tips=tips)
        if select:
            cur = next((r for r, p in enumerate(points) if p["current"]), None)
            if cur is not None:
                t.selectRow(cur)
                t.scrollToItem(t.item(cur, 0), QAbstractItemView.ScrollHint.EnsureVisible)
        t.blockSignals(False)

    def _render_check(self, v: dict) -> None:
        h = v["headers"]
        legacy = v["legacy"]
        heads = [h["item"], cal_view.C["graph_before"], cal_view.C["graph_after"], cal_view.C["col_diff"]]
        if legacy:
            heads.append(h["measured"])
        heads.append("")
        items = v["items"]
        self.items.setColumnCount(len(heads))
        self.items.setHorizontalHeaderLabels(heads)
        for i in range(len(heads)):
            self.items.horizontalHeaderItem(i).setTextAlignment(_RIGHT if 0 < i < len(heads) - 1 else _LEFT)
        self.items.setRowCount(len(items))
        warn_color = self.win.pal.get("warn")
        for r, it in enumerate(items):
            cells = [it["name"], it["before"], it["after"], it["diff"]] + ([it["measured"]] if legacy else [])
            note = it["warn"] or (C["big_mark"] if it["big"] else "")
            cells.append(note)
            _set_row(self.items, r, cells, tuple(range(1, len(cells) - 1)),
                     warn_cols=(len(cells) - 1, 3) if (it["warn"] or it["big"]) else (), warn_color=warn_color,
                     tips={len(cells) - 1: it["warn"] or (v["big_tip"] if it["big"] else "")})
        self.items.setFixedHeight(sizes.ROW_H * (len(items) + 1) + 4)
        g = v.get("graph")
        self.check_area.setVisible(g is not None)
        if g is not None:
            # band の境目の2行は、どちらの帯の欄かの列で分かる（K §4.2.1。PlutoSDR の利得の補正には無い）
            band = v["band_column"]
            pheads = ([h["freq"]] + ([h["band"]] if band else []) + [h["before"], h["after"], h["diff"]]
                      + ([h["measured"]] if legacy else []) + [""])
            nb = 1 if band else 0
            self._fill_points(self.check_points, v["points"], pheads,
                              lambda p: [p["freq"]] + ([p["band"]] if band else [])
                              + [p["before"], p["after"], p["diff"]]
                              + ([p["measured"]] if legacy else [])
                              + [p["warn"] or (C["big_mark"] if p["big"] else "")],
                              right=(0, 1 + nb, 2 + nb, 3 + nb) + ((4 + nb,) if legacy else ()), warn=legacy)
            self._draw_graph(g)
        self.check_filler.setVisible(g is None)
        self.save_reason.setText(v.get("save_reason") or "")
        self.save_reason.setVisible(bool(v.get("save_reason")))

    def _draw_graph(self, g: dict) -> None:
        sig = (tuple(g["before"]), tuple(g["after"]), tuple(g["marks"]))
        if sig == self._check_sig:
            return
        self._check_sig = sig
        pal = self.win.pal
        self.graph.setBackground(pal["graph_bg"])
        pi = self.graph.getPlotItem()
        pi.setLabel("bottom", g["x_label"])
        pi.setLabel("left", g["y_label"])
        pi.showGrid(x=True, y=True, alpha=0.3)
        for ax in ("bottom", "left"):
            pi.getAxis(ax).setPen(pg.mkPen(pal["axis"]))
            pi.getAxis(ax).setTextPen(pg.mkPen(pal["text_dim"]))
        self.legend.clear()
        self.curve_before.setData(g["freqs_mhz"], g["before"],
                                  pen=pg.mkPen(pal["text_dim"], width=2, style=Qt.PenStyle.DashLine))
        self.curve_after.setData(g["freqs_mhz"], g["after"], pen=pg.mkPen(pal["accent"], width=2))
        self.legend.addItem(self.curve_before, g["before_label"])
        self.legend.addItem(self.curve_after, g["after_label"])
        xs = [g["freqs_mhz"][i] for i in g["marks"]]
        ys = [g["after"][i] for i in g["marks"]]
        self.marks.setData(xs, ys, pen=pg.mkPen(pal["warn"]), brush=pg.mkBrush(pal["warn"]))
        pi.setXRange(0, g["x_max"], padding=0.01)

    def _render_buttons(self, v: dict) -> None:
        page = self.page
        self.btn_back.setEnabled(page in (MEASURE, CHECK) and not v["measuring"])
        if page == PREPARE:
            self.btn_next.setText(C["next"])
            self._enable(self.btn_next, v["next_reason"])
        elif page == MEASURE:
            self.btn_next.setText(C["next"])
            self._enable(self.btn_next, C["measuring"] if v["measuring"] else None)
        elif page == CHECK:
            self.btn_next.setText(C["save"])
            self._enable(self.btn_next, v.get("save_reason") or (C["measuring"] if v["measuring"] else None))
        else:
            self.btn_next.setText(C["again"])
            self._enable(self.btn_next, None)
        sizes.fit_button(self.btn_next)

    @staticmethod
    def _enable(b: QPushButton, why: Optional[str]) -> None:
        """使えないときは無効にして理由をツールチップに出す（U-08）。"""
        b.setEnabled(why is None)
        b.setToolTip(why or "")

    # ------------------------------------------------------------------ 操作

    def _ok(self, r: Optional[dict] = None) -> None:
        if isinstance(r, dict) and r.get("calibration_work") is not None:
            self.apply_state({**self.win.state, "calibration_work": r["calibration_work"]})

    def _set(self, field=None, **kw) -> None:
        self.win.call("cal_set", on_ok=self._ok, field=field, **kw)

    def _measure(self, state: Optional[str] = None) -> None:
        kw = {"state": state} if state else {}
        self.win.call("cal_measure", **kw)

    def _cancel_task(self) -> None:
        tid = (self.v or {}).get("task_id")
        if tid:
            self.win.call("cancel_task", task_id=tid, quiet_codes=("unknown_task",))

    def _point_selected(self) -> None:
        if self.v is None:
            return
        rows = self.points.selectionModel().selectedRows()
        if not rows:
            return
        i = rows[0].row()
        cur = next((p["index"] for p in self.v["points"] if p["current"]), None)
        if i != cur and not self.v["measuring"] and not self.v.get("pending"):
            self._set(current=i)

    def _choose_kind(self, kind: str) -> None:
        v = self.v
        if v is None or kind == v["kind"]:
            return
        if v["unsaved"]:
            if not self.win.confirm(v["title"], v["switch_question"], C["do_switch"]):
                self.kind_radios[v["kind"]].setChecked(True)
                return
        self.win.call("cal_set_kind", on_ok=self._ok, kind=kind, discard=True)

    def _choose_file(self) -> None:
        paths = self.win.ask_open_paths(C["file_title"], "", texts.LABELS["filter_txt"])
        if paths:
            self.win.call("cal_legacy_load", on_ok=self._ok, path=paths[0])

    def _ask_pending(self) -> None:
        pend = (self.v or {}).get("pending")
        if not pend:
            return
        accept = self.win.confirm(pend["title"], pend["text"], pend["ok"])
        self.win.call("cal_confirm", on_ok=self._ok, quiet_codes=("calibration_no_pending",), accept=bool(accept))

    def _back(self) -> None:
        if self.page in (MEASURE, CHECK):
            self.page -= 1
            self._render()

    def _next(self) -> None:
        if self.v is None:
            return
        if self.page in (PREPARE, MEASURE):
            self.page += 1
            self._render()
        elif self.page == CHECK:
            self.win.call("cal_save", on_ok=self._saved)
        else:
            self.page = PREPARE
            self._render()

    def _saved(self, r: dict) -> None:
        self.page = SAVED
        self._ok(r)

    def show_page(self, page: int) -> None:
        self.page = page
        if self.v is not None:
            self._render()

    def keyPressEvent(self, e) -> None:  # noqa: N802
        # Enter で「測る」（K §4.2）。欄の中の Enter は欄の確定（U-13）なので、欄にいないときだけ
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not isinstance(self.focusWidget(), QLineEdit) and self.page == MEASURE and self.v \
                    and self.v["kind"] == "freq" and self.btn_measure.isEnabled():
                self._measure()
            e.accept()
            return
        super().keyPressEvent(e)

    # ------------------------------------------------------------------ 閉じる（U-18）

    def ask_close(self) -> Optional[str]:
        """測った値があるときに聞く。"save"（測った点だけ・測った値を保存して閉じる）・"discard"・None（やめる）。"""
        v = self.v
        box = QMessageBox(QMessageBox.Icon.Question, v["title"], v["close_question"],
                          QMessageBox.StandardButton.NoButton, self)
        save = box.addButton(v["close_save_label"], QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton(C["do_close_discard"], QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton(texts.LABELS["cancel"], QMessageBox.ButtonRole.RejectRole)
        save.setEnabled(bool(v["close_savable"]))
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        return {save: "save", discard: "discard"}.get(box.clickedButton())

    def reject(self) -> None:
        self.close()  # Escape も ✕ と同じく、何が失われるかを聞いてから閉じる

    def closeEvent(self, e) -> None:  # noqa: N802
        # QDialog.closeEvent は reject() を呼ぶ（ここでは close に回している）ので使わず、閉じてよいときは受ける
        if self._closing or self.v is None:
            e.accept()
            return
        if self.v["unsaved"]:
            ans = self.ask_close()
            if ans is None:
                e.ignore()
                return
            if ans == "save":
                e.ignore()
                self.win.call("cal_save", on_ok=lambda _r: self._close_session())
                return
        self._close_session(e)

    def _close_session(self, e=None) -> None:
        self.win.call("cal_close")
        self.v = None
        self.page = PREPARE
        if e is not None:
            e.accept()
        else:
            self._closing = True
            self.close()
            self._closing = False


def open_calibration(win: "MainWindow", kind: str) -> CalDialog:
    """メニュー「校正」の項目から開く。窓は1つだけ。保存していない作業があれば、その作業のまま出す（U-18）。

    窓は作業ができてから出す（Web と同じ）: コアは校正データを読んでから作業を作るので、それより前に届いた状態には
    作業が無い。先に窓を出すと、その状態を「別の画面で閉じた」と取り違えて閉じてしまう（掃引中は状態が次々に届く）
    """
    d = win.cal_window
    if d is None:
        d = CalDialog(win)
        win.cal_window = d
    if not d.isVisible():
        d.page = PREPARE

    def opened(r: Optional[dict]) -> None:
        # 出してから描く: 確かめ待ちの点（Web で進めた作業）は、窓が見えているときに描くと聞く
        d.show()
        d.raise_()
        d.activateWindow()
        d._ok(r)

    win.call("cal_open", on_ok=opened, kind=kind)
    return d
