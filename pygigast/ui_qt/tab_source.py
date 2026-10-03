"""右のタブ「信号源」（設計J §4.5・L・E §6）。

- SG: 周波数・周波数を変えたら最大・最小保持を消すか（設計F §8）・出力中の表示
- ステップ掃引（設計L §1.3）: SG の欄のすぐ下（step_panel.py）
- ノーマライズの基準（TG・S11）: いまの基準の状態（名前・精度・経過時間・電源の入れ直し前）・一覧と選択・名前・消す・
  「広く取る」「いまの条件で細かく取る」（進み具合とやめるは処理の帯: H §4.1）・S11 のオープン/ショート
- NF: 次の回
- 大きさと余白は sizes.py の決まり（J §4.11）。名前は欄の左（S-05）、ボタンは文字の幅で左から詰める（S-03）
"""
from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QWidget,
)

from . import sizes, texts, widget_sync
from .fields import FreqField
from .step_panel import StepPanel
from .tab_traces import body, outer, set_enabled

if TYPE_CHECKING:
    from .main_window import MainWindow

REASON_REF_MODE = texts.ERRORS["mode_not_normalizable"]
REASON_NOT_CONNECTED = texts.REASON_TEXTS["needs_connection"]
REASON_TAKING = texts.REASON_TEXTS["taking_reference"]
# まだ作っていないもの（S11 のオープン・ショート: 設計E §6.5、NF: 設計M）
REASON_S11 = texts.NOT_YET["s11"]
REASON_NF = texts.NOT_YET["nf"]
REASON_NO_SELECTION = texts.REASON_TEXTS["select_reference"]
L = texts.LABELS
ID_ROLE = Qt.ItemDataRole.UserRole
# 基準の一覧の高さの下限（数行）
REF_LIST_MIN_H = 90


def _when(ts) -> str:
    if not ts:
        return ""
    return datetime.datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")


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
        self.sg_clear_holds.setToolTip(L["sg_clear_holds_tip"])
        self.sg_clear_holds.setChecked(True)
        self.sg_clear_holds.toggled.connect(
            lambda on: win.call("set_trace_options", sg_clears_holds=bool(on)))
        form.addRow(self.sg_clear_holds)
        self.sg_state = QLabel("")
        self.sg_state.setObjectName("dim")
        form.addRow(self.sg_state)
        lay.addWidget(g)
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
        v.addLayout(form)
        self.take_wide = QPushButton(L["take_wide"])
        self.take_fine = QPushButton(L["take_fine"])
        self.take_wide.clicked.connect(lambda: self._take("wide"))
        self.take_fine.clicked.connect(lambda: self._take("fine"))
        v.addLayout(sizes.buttons_row(self.take_wide, self.take_fine))
        self.s11_open = QPushButton(L["s11_open"])
        self.s11_short = QPushButton(L["s11_short"])
        set_enabled(self.s11_open, REASON_S11)
        set_enabled(self.s11_short, REASON_S11)
        v.addLayout(sizes.buttons_row(self.s11_open, self.s11_short))
        self.ref_list = QListWidget()
        # 一覧は数行が見える高さを下限にする（スクロールの中で潰れて行が見えなくならない）
        self.ref_list.setMinimumHeight(REF_LIST_MIN_H)
        self.ref_list.currentItemChanged.connect(lambda *_: self._update_buttons())
        self.ref_list.itemDoubleClicked.connect(lambda _it: self._select())
        v.addWidget(self.ref_list)
        self.btn_select = QPushButton(L["use"])
        self.btn_unselect = QPushButton(L["not_use"])
        self.btn_rename = QPushButton(L["rename_dots"])
        self.btn_delete = QPushButton(L["erase_dots"])
        self.btn_select.clicked.connect(self._select)
        self.btn_unselect.clicked.connect(lambda: win.call("reference_select", id=None, on_ok=lambda r: self.refresh()))
        self.btn_rename.clicked.connect(self._rename)
        self.btn_delete.clicked.connect(self._delete)
        v.addLayout(sizes.buttons_row(self.btn_select, self.btn_unselect, self.btn_rename, self.btn_delete))
        lay.addWidget(g)

        g = QGroupBox("NF")
        v = sizes.vbox(g)
        self.enr = QLineEdit("")
        self.enr.setPlaceholderText("ENR (dB)")
        sizes.set_width(self.enr, "value")
        set_enabled(self.enr, REASON_NF)
        v.addWidget(self.enr)
        lay.addWidget(g)
        lay.addStretch(1)
        outer(self, inner)
        self._refs: list[dict] = []

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
        # ステップ掃引の間は SG の周波数を掃引が動かす（欄は理由付きで変えられない: U-08）
        set_enabled(self.f_sg, texts.REASON_TEXTS["step_running"] if (st.get("step_sweep") or {}).get("running")
                    else None)
        self.step.apply_state(st)
        n = st.get("normalization") or {}
        sel = n.get("selected")
        text = texts.normalization_text(n)
        if not text:
            # 無いこと自体が情報（U-15）
            text = texts.w("ref_selected", name=sel["name"], when=_when(sel.get("taken_at"))) if sel else L["ref_none"]
        self.ref_state.setText(text)
        conn = st["connection"]
        serial = (conn.get("identity") or {}).get("serial")
        if serial != self._serial:
            self._serial = serial
            self.refresh()
        mode = sw["mode"]
        taking = any(t.get("kind") == "reference_take" for t in st.get("tasks") or [])
        reason = None
        if conn["state"] != "connected":
            reason = REASON_NOT_CONNECTED
        elif mode not in ("tg", "s11"):
            reason = REASON_REF_MODE
        elif taking:
            reason = REASON_TAKING
        tip_w = L["take_wide_tip"]
        tip_f = L["take_fine_tip"]
        set_enabled(self.take_wide, reason, tip_w)
        set_enabled(self.take_fine, reason, tip_f)
        self._update_buttons()

    def refresh(self) -> None:
        """基準の一覧を読み直す（機器が決まっていなければ空）。"""
        def show(r: dict) -> None:
            self._refs = r.get("references") or []
            cur = self.ref_list.currentItem().data(ID_ROLE) if self.ref_list.currentItem() else None
            self.ref_list.clear()
            for ref in self._refs:
                mark = "● " if ref.get("selected") else "   "
                it = QListWidgetItem(f"{mark}{ref['name']}  {ref['mode'].upper()}  {_when(ref.get('taken_at'))}")
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

    def _update_buttons(self) -> None:
        has = self._current_id() is not None
        for b in (self.btn_select, self.btn_rename, self.btn_delete):
            set_enabled(b, None if has else REASON_NO_SELECTION)
        selected = any(r.get("selected") for r in self._refs)
        set_enabled(self.btn_unselect, None if selected else texts.REASON_TEXTS["no_reference_selected"])

    # ---------------------------------------------------------------- 操作

    def _take(self, kind: str) -> None:
        name = self.take_name.text().strip() or None
        self.win.call("reference_take", kind=kind, name=name, averages=self.take_avg.value())

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

    def _delete(self) -> None:
        rid = self._current_id()
        ref = next((r for r in self._refs if r["id"] == rid), None)
        if ref is None:
            return
        extra = L["ref_delete_in_use"] if ref.get("selected") else ""
        if self.win.confirm(L["ref_delete_title"], texts.w("ref_delete_confirm", name=ref["name"]) + extra, L["do_delete"]):
            self.win.call("reference_delete", id=rid, on_ok=lambda r: self.refresh())
