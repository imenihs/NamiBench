"""機器の一覧の窓（設計J §4.7）。種類を問わず1つの一覧から選んで繋ぐ。

- 自動で開くのは繋がっていないときだけ（起動して前回の機器へ自動で繋げなかった・利用者が切断した）。
  抜けたとき（切断・再接続待ち）は開かない（同じ機器が戻るのを待つ: 設計B §7.3）。いつでもメニュー
  「機器 → 機器に接続…」から開ける。開いた形は main_window の都合で決めず、ここの open_list に1つにまとめる
- 1行は「名前・［詳細］［名前…］［接続］」の下に状態。COM 番号・USB ID は「詳細」の中にたたむ。名前は［名前…］で付けられる
- 挿し抜きで一覧が自動で変わる（コアのイベント `devices`。開いている間は list_devices の watch_s で見続けてもらう）。
  変わることは一覧が変わることで分かるので、文では言わない（J §4.12）
- 繋いでいる最中は「繋いでいます…」を出し、失敗したら理由と次の手を出す
- 何も挿していないときは案内と［シミュレータで試す］
- 文言は texts（texts_devices.py）。Web と同じ関数で行の文を作る
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QToolButton,
    QWidget,
)

from . import sizes, texts
from .texts_devices import DEVICE_LABELS as L

if TYPE_CHECKING:
    from .main_window import MainWindow

# 開いている間、コアに一覧を見続けてもらう長さと、延ばし直す間隔（秒）
WATCH_S = 10.0
RENEW_MS = 5000


class _Row(QFrame):
    """一覧の1行。"""

    def __init__(self, owner: "DeviceListWindow", d: dict, expanded: bool) -> None:
        super().__init__()
        self.setObjectName("deviceRow")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.d = d
        v = d["view"]
        g = QGridLayout(self)
        g.setContentsMargins(sizes.INSET, sizes.INSET, sizes.INSET, sizes.INSET)
        g.setHorizontalSpacing(sizes.GAP)
        g.setVerticalSpacing(sizes.GAP)
        self.name = QLabel(v["name"])
        f = QFont(self.name.font())
        f.setBold(True)
        f.setPointSize(f.pointSize() + 1)
        self.name.setFont(f)
        self.state = QLabel(v["state_text"])
        self.state.setObjectName("warn" if v["state_cls"] == "warn" else "dim")
        self.state.setWordWrap(True)
        self.more = QToolButton()
        self.more.setText(L["details"])
        self.more.setCheckable(True)
        self.more.setChecked(expanded)
        self.more.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.more.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.details = QLabel(v["details"])
        self.details.setObjectName("dim")
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.details.setVisible(expanded)
        self.more.toggled.connect(self._toggle)
        self.rename = QPushButton(L["rename"])
        self.rename.setVisible(v["renamable"])
        self.rename.clicked.connect(lambda: owner.rename(d))
        self.action = QPushButton(v["action_label"])
        self.action.setEnabled(v["action_reason"] is None)
        self.action.setToolTip(v["action_reason"] or "")
        self.action.clicked.connect(lambda: owner.act(d))
        # 1行目: 名前と操作（同じ高さにそろえる）。2行目: 状態。3行目: 詳細
        g.addWidget(self.name, 0, 0)
        g.addWidget(self.more, 0, 1)
        g.addWidget(self.rename, 0, 2)
        g.addWidget(self.action, 0, 3)
        g.addWidget(self.state, 1, 0, 1, 4)
        g.addWidget(self.details, 2, 0, 1, 4)
        g.setColumnStretch(0, 1)
        self._owner = owner

    def _toggle(self, on: bool) -> None:
        self.more.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)
        self.details.setVisible(on)
        self._owner.expanded[self.d["id"]] = on


class DeviceListWindow(QDialog):
    """機器の一覧（モードレス。開いたまま測定の画面も触れる）。"""

    def __init__(self, win: "MainWindow") -> None:
        super().__init__(win)
        self.win = win
        self.setWindowTitle(L["title"])
        self.setModal(False)
        self.resize(640, 420)
        self.expanded: dict[str, bool] = {}
        self.devices: list[dict] = []
        self._key: Optional[list] = None
        self.busy: Optional[dict] = None  # 繋いでいる最中の行

        self.empty = QWidget()
        ev = sizes.vbox(self.empty, inset=False)
        self.empty_label = QLabel(L["empty"])
        f = QFont(self.empty_label.font())
        f.setPointSize(f.pointSize() + 2)
        self.empty_label.setFont(f)
        self.empty_label.setWordWrap(True)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.try_sim = QPushButton(L["try_sim"])
        self.try_sim.clicked.connect(self._try_sim)
        ev.addStretch(1)
        ev.addWidget(self.empty_label)
        ev.addSpacing(sizes.SECTION_GAP)
        ev.addWidget(self.try_sim, 0, Qt.AlignmentFlag.AlignHCenter)
        ev.addStretch(1)

        self.rows_box = QWidget()
        self.rows = sizes.vbox(self.rows_box, inset=False)
        self.rows.addStretch(1)
        self.scroll = scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.rows_box)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.hide()
        close = QPushButton(L["close"])
        close.clicked.connect(self.close)
        bottom = sizes.hbox()
        bottom.addWidget(self.status, 1)
        bottom.addWidget(close, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)

        lay = sizes.vbox(self, inset=False)
        sizes.window(lay)
        lay.addWidget(self.empty, 1)
        lay.addWidget(scroll, 1)
        lay.addLayout(bottom)

        self._timer = QTimer(self)
        self._timer.setInterval(RENEW_MS)
        self._timer.timeout.connect(self.refresh)

    # ------------------------------------------------------------------ 一覧

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.refresh()
        self._timer.start()

    def hideEvent(self, e) -> None:
        self._timer.stop()
        super().hideEvent(e)
        # 閉じたら測定の画面へ戻る（キー操作がすぐ効くように）
        if self.win.isVisible():
            self.win.activateWindow()

    def refresh(self) -> None:
        """一覧を読み直し、開いている間はコアに見続けてもらう（期限を延ばす）。"""
        self.win.call("list_devices", on_ok=lambda r: self.set_devices(r.get("devices") or []), watch_s=WATCH_S)

    def set_devices(self, devices: list[dict]) -> None:
        self.devices = devices
        view = texts.devices_view(devices)
        key = [(d["id"], d["view"]) for d in view["rows"]]
        self.empty.setVisible(view["empty"])
        self.scroll.setVisible(bool(view["rows"]))  # DLL の行だけでも出す
        self.try_sim.setEnabled(view["simulator"] is not None and self.busy is None)
        if key == self._key:
            return
        self._key = key
        while self.rows.count() > 1:
            w = self.rows.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        for d in view["rows"]:
            row = _Row(self, d, self.expanded.get(d["id"], False))
            row.action.setEnabled(row.action.isEnabled() and self.busy is None)
            self.rows.insertWidget(self.rows.count() - 1, row)

    def row_widgets(self) -> list[_Row]:
        return [self.rows.itemAt(i).widget() for i in range(self.rows.count() - 1)]

    # ------------------------------------------------------------------ 操作

    def _set_busy(self, d: Optional[dict]) -> None:
        self.busy = d
        for r in self.row_widgets():
            r.action.setEnabled(d is None and r.d["view"]["action_reason"] is None)
        self.try_sim.setEnabled(d is None)

    def _show_status(self, text: str, warn: bool = False) -> None:
        self.status.setText(text)
        self.status.setObjectName("warn" if warn else "")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.status.setVisible(bool(text))

    def act(self, d: dict) -> None:
        if d.get("state") == "dll_not_found":
            self.choose_dll(d.get("kind") or "rtlsdr")
        else:
            self.connect_to(d)

    def _try_sim(self) -> None:
        sim = texts.devices_view(self.devices)["simulator"]
        if sim is not None:
            self.connect_to(sim)

    def connect_to(self, d: dict) -> None:
        if self.busy is not None:
            return
        name = texts.device_name(d)
        self._set_busy(d)
        self._show_status(texts.tpl("device_connecting", name=name))
        fut = self.win.api.call("connect", backend=d["kind"], serial=d.get("serial") or None, port=d.get("port"))

        def done(f) -> None:
            def in_gui() -> None:
                self._set_busy(None)
                exc = f.exception()
                if exc is None:
                    self._show_status("")
                    self.close()
                    return
                code = getattr(exc, "code", None)
                self._show_status(texts.connect_failed_text(name, texts.error_text(exc), code), warn=True)
                self.refresh()

            self.win._bridge.call.emit(in_gui)

        fut.add_done_callback(done)

    def rename(self, d: dict) -> None:
        text = self.win.ask_text(L["rename_title"], L["rename_label"], d.get("custom_name") or d.get("name") or "")
        if text is None:
            return
        self.win.call("rename_device", kind=d["kind"], serial=d["serial"], name=text.strip() or None)

    def choose_dll(self, kind: str = "rtlsdr") -> None:
        pluto = kind == "plutosdr"
        folder = self.win.ask_folder(L["dll_folder_title_plutosdr" if pluto else "dll_folder_title"])
        if folder:
            self.win.call("plutosdr_set" if pluto else "rtlsdr_set", on_ok=lambda r: self.refresh(), dll_folder=folder)
