"""右のタブ「信号源」（設計J §4.5・L・E §6）。

- SG: 周波数・出力中の表示（ステップ掃引は次の回）
- TG・S11 の基準: いまの基準の状態（名前・精度・経過時間・電源の入れ直し前）・一覧と選択・名前・消す・
  「広く取る」「いまの条件で細かく取る」（進み具合とやめるは処理の帯: H §4.1）・S11 のオープン/ショート
- NF: 次の回
"""
from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import texts
from .fields import FreqField
from .tab_traces import scroll, set_enabled

if TYPE_CHECKING:
    from .main_window import MainWindow

REASON_REF_MODE = "基準は TG・S11 モードで取ります"
REASON_NOT_CONNECTED = "機器に繋ぐと使えます"
REASON_TAKING = "基準を取っている間は使えません"
REASON_S11 = "S11（反射）モードは次の回で実装します（設計E §6.5）"
REASON_STEP = "ステップ掃引は次の回で実装します（設計L）"
REASON_NF = "NF 測定は次の回で実装します（設計M）"
REASON_NO_SELECTION = "一覧から基準を選んでください"
ID_ROLE = Qt.ItemDataRole.UserRole


def _when(ts) -> str:
    if not ts:
        return ""
    return datetime.datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")


class SourceTab(QWidget):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        self._serial: Optional[str] = None
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(4, 4, 4, 4)

        g = QGroupBox("SG（信号源）")
        form = QFormLayout(g)
        self.f_sg = FreqField("SG 周波数", self._set_sg, big=False)
        form.addRow(self.f_sg)
        self.sg_state = QLabel("")
        self.sg_state.setObjectName("dim")
        form.addRow(self.sg_state)
        self.step_sweep = QPushButton("ステップ掃引…")
        set_enabled(self.step_sweep, REASON_STEP)
        form.addRow(self.step_sweep)
        lay.addWidget(g)

        g = QGroupBox("TG・S11 の基準（ノーマライズ）")
        v = QVBoxLayout(g)
        self.ref_state = QLabel("")
        self.ref_state.setWordWrap(True)
        v.addWidget(self.ref_state)
        grid = QGridLayout()
        grid.addWidget(QLabel("名前"), 0, 0)
        self.take_name = QLineEdit("")
        self.take_name.setPlaceholderText("空なら「TG 日時」")
        grid.addWidget(self.take_name, 0, 1, 1, 2)
        grid.addWidget(QLabel("平均"), 1, 0)
        self.take_avg = QSpinBox()
        self.take_avg.setRange(1, 64)
        self.take_avg.setSuffix(" 回")
        grid.addWidget(self.take_avg, 1, 1)
        self.take_wide = QPushButton("広く取る")
        self.take_fine = QPushButton("いまの条件で細かく取る")
        self.take_wide.clicked.connect(lambda: self._take("wide"))
        self.take_fine.clicked.connect(lambda: self._take("fine"))
        grid.addWidget(self.take_wide, 2, 0, 1, 2)
        grid.addWidget(self.take_fine, 2, 2)
        self.s11_open = QPushButton("S11: オープンを測る")
        self.s11_short = QPushButton("S11: ショートを測る")
        set_enabled(self.s11_open, REASON_S11)
        set_enabled(self.s11_short, REASON_S11)
        grid.addWidget(self.s11_open, 3, 0, 1, 2)
        grid.addWidget(self.s11_short, 3, 2)
        v.addLayout(grid)
        self.ref_list = QListWidget()
        self.ref_list.setMinimumHeight(90)
        self.ref_list.currentItemChanged.connect(lambda *_: self._update_buttons())
        self.ref_list.itemDoubleClicked.connect(lambda _it: self._select())
        v.addWidget(self.ref_list)
        row = QHBoxLayout()
        self.btn_select = QPushButton("使う")
        self.btn_unselect = QPushButton("使わない")
        self.btn_rename = QPushButton("名前…")
        self.btn_delete = QPushButton("消す…")
        for b in (self.btn_select, self.btn_unselect, self.btn_rename, self.btn_delete):
            b.setObjectName("small")
            row.addWidget(b)
        self.btn_select.clicked.connect(self._select)
        self.btn_unselect.clicked.connect(lambda: win.call("reference_select", id=None, on_ok=lambda r: self.refresh()))
        self.btn_rename.clicked.connect(self._rename)
        self.btn_delete.clicked.connect(self._delete)
        v.addLayout(row)
        lay.addWidget(g)

        g = QGroupBox("NF")
        v = QVBoxLayout(g)
        self.enr = QLineEdit("")
        self.enr.setPlaceholderText("ENR (dB)")
        set_enabled(self.enr, REASON_NF)
        v.addWidget(self.enr)
        lay.addWidget(g)
        lay.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll(inner))
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
        self.sg_state.setText(self.win.source_text() or "出力していません（SG モードで出力します）")
        n = st.get("normalization") or {}
        sel = n.get("selected")
        text = texts.normalization_text(n)
        if not text:
            # 無いこと自体が情報（U-15）
            text = f"選んでいる基準: {sel['name']}（{_when(sel.get('taken_at'))}）" if sel else "ノーマライズ: 未取得"
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
        tip_w = "TG の範囲を 1MHz 刻みで取る（TG 出力と SP 入力を直結してから）"
        tip_f = "いまの Start・Stop・Step でその範囲だけ取り、いまの基準へ上書き合成する"
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
        set_enabled(self.btn_unselect, None if selected else "選んでいる基準がありません")

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
        name = self.win.ask_text("基準の名前", "新しい名前", ref["name"])
        if name and name.strip() and name != ref["name"]:
            self.win.call("reference_rename", id=rid, name=name.strip(), on_ok=lambda r: self.refresh())

    def _delete(self) -> None:
        rid = self._current_id()
        ref = next((r for r in self._refs if r["id"] == rid), None)
        if ref is None:
            return
        extra = "\nいま使っている基準なので、TG の表示は受けた電力（dBm）に戻ります。" if ref.get("selected") else ""
        if self.win.confirm("基準を消す", f"基準「{ref['name']}」を消します。取り直すまで戻せません。{extra}"):
            self.win.call("reference_delete", id=rid, on_ok=lambda r: self.refresh())
