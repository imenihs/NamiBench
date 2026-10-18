"""右のタブ「信号源」（設計J §4.5・L・E §6）。

- SG: 周波数・周波数を変えたら最大・最小保持を消すか（設計F §8）・出力中の表示
- ステップ掃引（設計L §1.3）: SG の欄のすぐ下（step_panel.py）
- ノーマライズの基準（TG・S11）: いまの基準の状態（名前・基準の値・精度・経過時間・電源の入れ直し前）・一覧と選択・
  名前・基準の値（測り直さずに直す: 設計E §6.7）・消す・TG の「基準の値」の欄（取るときに送る。上段の「基準を取る」も
  この値を使う）と「広く取る」「いまの条件で細かく取る」（進み具合とやめるは処理の帯: H §4.1）・S11 の範囲とオープン → ショート
  （1段ずつ。次に押すボタンを強調し、測ったオープンの時刻と範囲を出す: 設計E §6.5。中身は screen_s11.s11_view）
- NF（設計M）: ENR（dB の小数）・ENR の表（CSV を読み込む・外す）・NF の増幅度（校正データの倍率）・つなぎ方の窓を開く。
  中身は nf_view.nf_view（Web と同じ）。ENR が無いときの知らせは ENR の欄に、表の範囲の知らせは表の行に付ける（U-25）
- 大きさと余白は sizes.py の決まり（J §4.11）。名前は欄の左（S-05）、ボタンは文字の幅で左から詰める（S-03）
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QWidget,
)

from ..core.units import ParseError, level_text, parse_db
from . import nf_view, screen_model, screen_s11, sizes, texts, widget_sync
from .fields import DbField, FreqField, LocalDbChoiceField
from .pluto_panel import PlutoTxPanel
from .step_panel import StepPanel
from .texts_nf import NF_TEXTS as N
from .tab_traces import body, outer, set_enabled

if TYPE_CHECKING:
    from .main_window import MainWindow

REASON_NO_SELECTION = texts.REASON_TEXTS["select_reference"]
L = texts.LABELS
ID_ROLE = Qt.ItemDataRole.UserRole
# 基準の一覧の高さの下限（数行）
REF_LIST_MIN_H = 90


# ENR の表を読み込めなかった理由を出しておく長さ（ミリ秒。Web の app_nf.js と同じ）
TABLE_NOTE_MS = 8000


def read_text(data: bytes) -> str:
    """CSV のバイト列を文字にする（UTF-8、読めなければ CP932。Web の app_nf.js と同じ順）。"""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp932", errors="replace")


class _EnrField(DbField):
    """ENR の欄（dB の小数を 0.01dB まで出す。値が無いときは空: nf_view.enr_text）。"""

    def __init__(self, name: str, on_commit) -> None:
        super().__init__(name, "dB", on_commit, big=False)

    def set_value(self, v: float) -> None:
        self._value = v
        self.edit.show_value(nf_view.enr_text(v))

    def clear_value(self) -> None:
        self._value = 0.0
        self.edit.show_value("")


class SourceTab(QWidget):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        self._serial: Optional[str] = None
        inner, lay = body()

        g = QGroupBox(L["sg_group"])
        form = sizes.form(g)
        self.f_sg = FreqField(L["sg_freq"], self._set_sg, big=False)
        form.addRow(self.f_sg.name, self.f_sg)
        # SG の周波数を変えたときに最大・最小保持も消すか（設計F §8。コアの設定で Qt と Web が共有）
        self.sg_clear_holds = QCheckBox(L["sg_clear_holds"])
        self.sg_clear_holds.setChecked(True)
        self.sg_clear_holds.toggled.connect(
            lambda on: win.call("set_trace_options", sg_clears_holds=bool(on)))
        form.addRow(self.sg_clear_holds)
        self.sg_state = QLabel("")
        self.sg_state.setObjectName("dim")
        form.addRow(self.sg_state)
        lay.addWidget(g)
        # 送信の減衰（PlutoSDR: SG・TG の出力のレベル。設計Q §7）。PlutoSDR に繋いでいるときだけ出す
        self.pluto_tx = PlutoTxPanel(win)
        lay.addWidget(self.pluto_tx)
        # SG のステップ掃引（設計L §1.3）。SG の周波数を動かす機能なので SG の欄のすぐ下
        self.step = StepPanel(win)
        lay.addWidget(self.step)

        g = QGroupBox(L["references"])
        v = sizes.vbox(g)
        self.ref_state = QLabel("")
        self.ref_state.setWordWrap(True)
        v.addWidget(self.ref_state)
        form = sizes.form()
        self.take_name = QLineEdit("")
        self.take_name.setPlaceholderText(L["take_name_placeholder"])
        sizes.set_width(self.take_name, "name")
        form.addRow(L["name"], self.take_name)
        self.take_avg = QSpinBox()
        self.take_avg.setRange(1, 64)
        self.take_avg.setSuffix(L["times_suffix"])
        sizes.set_width(self.take_avg, "int")
        form.addRow(L["average"], self.take_avg)
        # 基準の値（設計E §6.7: 表示 = 基準との比 + この値）。取るときに送る値で、画面ごとに持つ（名前・平均と同じ）
        spec = texts.REF_VALUE_SPEC
        self.take_value = LocalDbChoiceField(L["ref_value"], "dB", lambda _v: None, tuple(spec["choices"]),
                                             signed=spec["signed"], lo=spec["lo"], hi=spec["hi"],
                                             decimals=spec["decimals"], big=False)
        self.take_value.set_value(0.0)
        form.addRow(self.take_value.name, self.take_value)
        v.addLayout(form)
        self.take_wide = QPushButton(L["take_wide"])
        self.take_fine = QPushButton(L["take_fine"])
        self.take_wide.clicked.connect(lambda: self._take("wide"))
        self.take_fine.clicked.connect(lambda: self._take("fine"))
        v.addLayout(sizes.buttons_row(self.take_wide, self.take_fine))
        # S11 の基準: オープン → ショートを1段ずつ（設計E §6.5）。範囲はオープンで決め、ショートは同じ範囲で測る
        form = sizes.form()
        self.s11_range = QComboBox()
        self.s11_range.addItem(L["s11_range_wide"], "wide")
        self.s11_range.addItem(L["s11_range_fine"], "fine")
        self.s11_range.setToolTip(L["s11_range_tip"])
        form.addRow(L["s11_range"], self.s11_range)
        v.addLayout(form)
        self.s11_open = QPushButton(L["s11_open"])
        self.s11_short = QPushButton(L["s11_short"])
        self.s11_open.clicked.connect(lambda: self._take_s11("open"))
        self.s11_short.clicked.connect(lambda: self._take_s11("short"))
        v.addLayout(sizes.buttons_row(self.s11_open, self.s11_short))
        # 測ったオープンの時刻と範囲（次はショート）。測っていなければ出さない
        self.s11_state = QLabel("")
        self.s11_state.setObjectName("dim")
        self.s11_state.setWordWrap(True)
        self.s11_state.hide()
        v.addWidget(self.s11_state)
        self.ref_list = QListWidget()
        # 一覧は数行が見える高さを下限にする（スクロールの中で潰れて行が見えなくならない）
        self.ref_list.setMinimumHeight(REF_LIST_MIN_H)
        self.ref_list.currentItemChanged.connect(lambda *_: self._update_buttons())
        self.ref_list.itemDoubleClicked.connect(lambda _it: self._select())
        v.addWidget(self.ref_list)
        self.btn_select = QPushButton(L["use"])
        self.btn_unselect = QPushButton(L["not_use"])
        self.btn_rename = QPushButton(L["rename_dots"])
        self.btn_value = QPushButton(L["ref_value_dots"])
        self.btn_delete = QPushButton(L["erase_dots"])
        self.btn_select.clicked.connect(self._select)
        self.btn_unselect.clicked.connect(lambda: win.call("reference_select", id=None, on_ok=lambda r: self.refresh()))
        self.btn_rename.clicked.connect(self._rename)
        self.btn_value.clicked.connect(self._edit_value)
        self.btn_delete.clicked.connect(self._delete)
        v.addLayout(sizes.buttons_row(self.btn_select, self.btn_unselect, self.btn_rename, self.btn_value,
                                      self.btn_delete))
        lay.addWidget(g)

        lay.addWidget(self._build_nf())
        lay.addStretch(1)
        outer(self, inner)
        self._refs: list[dict] = []
        self._open_kind: Optional[str] = None

    # ---------------------------------------------------------------- NF（設計M）

    def _build_nf(self) -> QGroupBox:
        g = QGroupBox(N["group"])
        form = sizes.form(g)
        self.enr = _EnrField(N["enr"], self._set_enr)
        form.addRow(self.enr.name, self.enr)
        self.enr_warn = QLabel("")
        self.enr_warn.setObjectName("warn")
        self.enr_warn.hide()
        form.addRow("", self.enr_warn)
        self.enr_table = QLabel("")
        form.addRow(N["enr_table"], self.enr_table)
        self.table_warn = QLabel("")
        self.table_warn.setObjectName("warn")
        self.table_warn.hide()
        form.addRow("", self.table_warn)
        # 読み込めなかった理由（一度きり: U-05。表の行のそばに出して消す）
        self.table_note = QLabel("")
        self.table_note.setObjectName("warn")
        self.table_note.setWordWrap(True)
        self.table_note.hide()
        self._table_note_timer = QTimer(self)
        self._table_note_timer.setSingleShot(True)
        self._table_note_timer.timeout.connect(lambda: (self.table_note.setText(""), self.table_note.hide()))
        form.addRow("", self.table_note)
        self.load_table = QPushButton(N["load_table"])
        self.load_table.clicked.connect(self._load_table)
        self.clear_table = QPushButton(N["clear_table"])
        self.clear_table.clicked.connect(lambda: self.win.call("nf_clear_enr_table"))
        form.addRow("", sizes.buttons_row(self.load_table, self.clear_table))
        self.amp = QLabel("")
        form.addRow(N["amp"], self.amp)
        self.guide = QPushButton(N["guide_button"])
        self.guide.clicked.connect(self._open_guide)
        form.addRow(sizes.buttons_row(self.guide))
        return g

    def _apply_nf(self, st: dict) -> None:
        v = nf_view.nf_view(st)
        if v["enr_value"] is None:
            self.enr.clear_value()
        else:
            self.enr.set_value(v["enr_value"])
        set_enabled(self.enr.edit, v["enr_reason"], v["enr_tip"])
        self.enr_warn.setText(v["enr_warn"])
        self.enr_warn.setVisible(bool(v["enr_warn"]))
        self.enr_table.setText(v["table_text"])
        self.table_warn.setText(v["table_warn"])
        self.table_warn.setVisible(bool(v["table_warn"]))
        set_enabled(self.load_table, v["load_reason"])
        set_enabled(self.clear_table, v["clear_reason"])
        self.amp.setText(v["amp_text"])

    def _set_enr(self, db: float) -> None:
        self.win.call("nf_set_enr", field=self.enr, enr_db=float(db))

    def _load_table(self) -> None:
        paths = self.win.ask_open_paths(N["table_file_title"], "", N["table_file_filter"])
        if not paths:
            return
        from pathlib import Path

        p = Path(paths[0])
        try:
            data = p.read_bytes()
        except OSError:
            self.flash_table_note(texts.ERRORS["file_unreadable"])
            return
        self.send_table(read_text(data), p.name)

    def send_table(self, text: str, name: str) -> None:
        """表の中身を送る（Web と同じ命令。読めなければ理由を表の行のそばに出す）。"""
        fut = self.win.api.call("nf_load_enr_table", text=text, file_name=name)

        def done(f) -> None:
            def in_gui() -> None:
                exc = f.exception()
                if exc is not None:
                    self.flash_table_note(texts.error_text(exc))
                else:
                    self.flash_table_note("")

            self.win._bridge.call.emit(in_gui)

        fut.add_done_callback(done)

    def flash_table_note(self, text: str) -> None:
        self.table_note.setText(text)
        self.table_note.setVisible(bool(text))
        if text:
            self._table_note_timer.start(TABLE_NOTE_MS)

    def _open_guide(self) -> None:
        from .nf_dialog import open_guide

        open_guide(self.win)

    # ---------------------------------------------------------------- SG

    def _set_sg(self, hz: int) -> None:
        mode = (self.win.state.get("sweep") or {}).get("mode", "sg")
        self.win.call("set_mode", mode=mode, sg_hz=hz, field=self.f_sg)

    # ---------------------------------------------------------------- 状態

    def apply_state(self, st: dict) -> None:
        sw = st["sweep"]
        if sw.get("sg_hz") is not None:
            self.f_sg.set_hz(sw["sg_hz"])
        widget_sync.set_checked(self.sg_clear_holds, (st.get("trace_options") or {}).get("sg_clears_holds", True))
        self.sg_state.setText(texts.sg_state_text(self.win.state))
        # ステップ掃引の間は SG の周波数を掃引が動かす・再生中はライブの条件を変えない（欄は理由付きで変えられない: U-08）
        set_enabled(self.f_sg, screen_model.sg_reason(st))
        self.step.apply_state(st)
        self.pluto_tx.apply(st)
        self.ref_state.setText(screen_s11.ref_state_text(st))  # Web と同じ文
        conn = st["connection"]
        serial = (conn.get("identity") or {}).get("serial")
        if serial != self._serial:
            self._serial = serial
            self.refresh()
        # TG の「広く取る」「いまの条件で細かく取る」（S11 はオープン・ショートの2段で取る: 設計E §6.5）
        reason = screen_s11.tg_take_reason(st)
        set_enabled(self.take_wide, screen_s11.tg_wide_reason(st), L["take_wide_tip"])
        set_enabled(self.take_fine, reason, screen_s11.tg_fine_tip(st))  # PlutoSDR は利得を合わせてから（Web と同じ）
        rv = screen_s11.ref_value_view(st)  # S11 の基準には使わない（Web と同じ）
        set_enabled(self.take_value, rv["reason"], rv["tip"])
        v = screen_s11.s11_view(st)
        set_enabled(self.s11_range, v["range_reason"], L["s11_range_tip"])
        set_enabled(self.s11_open, v["open_reason"], v["open_tip"])
        set_enabled(self.s11_short, v["short_reason"], v["short_tip"])
        # 次に押すボタンを強調する（1段ずつ: U-03 の形で示す）
        for b, step in ((self.s11_open, "open"), (self.s11_short, "short")):
            on = v["next"] == step and v[step + "_reason"] is None
            if bool(b.property("primary")) != on:
                b.setProperty("primary", on)
                b.style().unpolish(b)
                b.style().polish(b)
        self.s11_state.setText(v["state_text"])
        self.s11_state.setVisible(bool(v["state_text"]))
        if v["range_fixed"] and self.s11_range.currentData() != v["range_fixed"]:
            # FFT の機器（PlutoSDR）は範囲をいまの条件に決めている（欄は理由付きで使えない）
            widget_sync.set_data(self.s11_range, v["range_fixed"])
        if v["open_kind"] != self._open_kind:
            # オープンを測ったら、範囲の欄はその範囲を出す（ショートは同じ範囲で測る）
            self._open_kind = v["open_kind"]
            if v["open_kind"]:
                widget_sync.set_data(self.s11_range, v["open_kind"])
        self._update_buttons()
        self._apply_nf(st)

    def refresh(self) -> None:
        """基準の一覧を読み直す（機器が決まっていなければ空）。"""
        def show(r: dict) -> None:
            self._refs = r.get("references") or []
            cur = self.ref_list.currentItem().data(ID_ROLE) if self.ref_list.currentItem() else None
            self.ref_list.clear()
            for ref in self._refs:
                it = QListWidgetItem(screen_s11.ref_row(ref)["text"])  # Web の表と同じ行の文（基準の値も）
                it.setData(ID_ROLE, ref["id"])
                self.ref_list.addItem(it)
                if ref["id"] == cur:
                    self.ref_list.setCurrentItem(it)
            self._update_buttons()

        if not self._serial:
            self._refs = []
            self.ref_list.clear()
            self._update_buttons()
            return
        self.win.call("reference_list", on_ok=show, quiet_codes=("device_not_selected",))

    def _current_id(self) -> Optional[str]:
        it = self.ref_list.currentItem()
        return it.data(ID_ROLE) if it else None

    def _current_ref(self) -> Optional[dict]:
        rid = self._current_id()
        return next((r for r in self._refs if r["id"] == rid), None)

    def _update_buttons(self) -> None:
        has = self._current_id() is not None
        for b in (self.btn_select, self.btn_rename, self.btn_delete):
            set_enabled(b, None if has else REASON_NO_SELECTION)
        ref = self._current_ref()
        set_enabled(self.btn_value, screen_s11.ref_row(ref)["value_reason"] if ref else REASON_NO_SELECTION)
        selected = any(r.get("selected") for r in self._refs)
        set_enabled(self.btn_unselect, None if selected else texts.REASON_TEXTS["no_reference_selected"])

    # ---------------------------------------------------------------- 操作

    def _take(self, kind: str) -> None:
        name = self.take_name.text().strip() or None
        self.win.call("reference_take", kind=kind, name=name, averages=self.take_avg.value(),
                      value_db=self.take_value.value())

    def _take_s11(self, standard: str) -> None:
        """S11 のオープン・ショート（設計E §6.5）。オープンは選んだ範囲、ショートはオープンと同じ範囲（コアが合わせる）。"""
        name = self.take_name.text().strip() or None
        kw = {"kind": self.s11_range.currentData()} if standard == "open" else {}
        self.win.call("reference_take", standard=standard, name=name, averages=self.take_avg.value(), **kw)

    def _select(self) -> None:
        rid = self._current_id()
        if rid:
            self.win.call("reference_select", id=rid, on_ok=lambda r: self.refresh())

    def _rename(self) -> None:
        rid = self._current_id()
        ref = next((r for r in self._refs if r["id"] == rid), None)
        if ref is None:
            return
        name = self.win.ask_text(L["rename_ref_title"], L["new_name"], ref["name"])
        if name and name.strip() and name != ref["name"]:
            self.win.call("reference_rename", id=rid, name=name.strip(), on_ok=lambda r: self.refresh())

    def _edit_value(self) -> None:
        """一覧で選んだ基準の値を直す（測り直さない: 設計E §6.7）。範囲の外はコアが断り、理由を出す。"""
        ref = self._current_ref()
        if ref is None or screen_s11.ref_row(ref)["value_reason"]:
            return
        text = self.win.ask_text(L["ref_value_title"], L["ref_value_new"], level_text(ref.get("value_db") or 0.0))
        if text is None or not text.strip():
            return
        try:
            v = parse_db(text)
        except ParseError:
            self.win.status_note(L["bad_value"])
            return
        if v != (ref.get("value_db") or 0.0):
            self.win.call("reference_set_value", id=ref["id"], value_db=v, on_ok=lambda r: self.refresh())

    def _delete(self) -> None:
        rid = self._current_id()
        ref = next((r for r in self._refs if r["id"] == rid), None)
        if ref is None:
            return
        extra = L["ref_delete_in_use"] if ref.get("selected") else ""
        if self.win.confirm(L["ref_delete_title"], texts.w("ref_delete_confirm", name=ref["name"]) + extra, L["do_delete"]):
            self.win.call("reference_delete", id=rid, on_ok=lambda r: self.refresh())
