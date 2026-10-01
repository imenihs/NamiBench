"""Qt の画面の「Web の口」（設計A §4.3.2・O §1.3）: 開く・LAN に開く・接続のしかたの窓・つながっている相手。

- Web の口を開くか・ポート番号は覚えておく（次の起動でも同じ）。**LAN に開くかは覚えない**（起動したら
  自分の PC だけに戻す。LAN に開くのは毎回の明示だけにする: A §4.3.2 の 1。設計に無い判断）
- LAN を ON にすると「接続のしかた」の窓を出す。窓を開くたびに新しい認証番号（2 分で無効）
- 状態バーに「Web: LAN に公開中・接続 N（操作 a・見るだけ b）」。押すと相手の一覧（時刻・IP・種類・［切断］）と
  ［認証を作り直す］
- 認証と接続の一覧は Web の口のスレッドで変わるので、知らせを Qt のシグナルで画面のスレッドへ渡し直す
"""
from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..api.web import qr
from ..api.web.access import KIND_OPERATE, KIND_VIEW
from . import menus, texts

if TYPE_CHECKING:
    from .main_window import MainWindow

DEFAULT_PORT = 8080
QR_SCALE = 5


class _Bridge(QObject):
    changed = Signal()


def qr_pixmap(text: str, scale: int = QR_SCALE) -> QPixmap:
    """QR コード（白い余白込み）の画像。"""
    m = qr.encode(text, "M")
    n = len(m) + 2 * qr.QUIET
    img = QImage(n * scale, n * scale, QImage.Format.Format_RGB32)
    img.fill(QColor("white"))
    p = QPainter(img)
    for y, row in enumerate(m):
        for x, v in enumerate(row):
            if v:
                p.fillRect((x + qr.QUIET) * scale, (y + qr.QUIET) * scale, scale, scale, QColor("black"))
    p.end()
    return QPixmap.fromImage(img)


class WebControl:
    """Web の口の持ち主（Qt の画面の側）。service は開いたときに作る WebService。"""

    def __init__(self, win: "MainWindow", options: Optional[dict] = None) -> None:
        self.win = win
        self.service = None
        self._bridge = _Bridge()
        self._bridge.changed.connect(self.refresh, Qt.ConnectionType.QueuedConnection)
        self._unsub = None
        self.connect_window: Optional[ConnectWindow] = None
        self.clients_window: Optional[ClientsWindow] = None
        prefs = win.prefs
        enabled = bool(options.get("enabled")) if options else _truthy(prefs.get("web_enabled", False))
        # 起動の引数で番号を指定しなければ、前回の番号（設定の画面で変えた番号を覚える）
        self.port = int((options or {}).get("port") or prefs.get("web_port", DEFAULT_PORT))
        if enabled:
            self.set_enabled(True)
            if options and options.get("lan"):
                self.set_lan(True)
        # 同じネットワークへ出す設定は覚えるが、起動したときは出さない（暗号化していないので、知らない
        # ネットワークで黙って公開しない）。前回出していたなら、切ったことを知らせる（黙って切らない）
        self.lan_was_on = _truthy(prefs.get("web_lan", False)) and not (options and options.get("lan"))
        if self.lan_was_on:
            prefs.set("web_lan", False)
            QTimer.singleShot(0, self._tell_lan_off)
        self.refresh()

    def _tell_lan_off(self) -> None:
        W = texts.WEB_LABELS
        box = QMessageBox(QMessageBox.Icon.Information, W["lan_off_at_start_title"], W["lan_off_at_start"],
                          QMessageBox.StandardButton.Close, self.win)
        go = box.addButton(W["open_settings"], QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(QMessageBox.StandardButton.Close)
        box.setModal(False)
        box.buttonClicked.connect(lambda b: self.win._settings_dialog("web") if b is go else None)
        box.show()
        self._lan_box = box

    # ---- 状態
    @property
    def enabled(self) -> bool:
        return self.service is not None and self.service.running

    @property
    def lan(self) -> bool:
        return self.service is not None and self.service.lan and self.service.running

    def view(self) -> dict:
        """設定の画面に出す中身。"""
        L = texts.WEB_LABELS
        s = self.service
        if s is not None and s.error:
            text, warn = texts.tpl_web("state_error", reason=texts.ERRORS.get(s.error, s.error)), True
        elif not self.enabled:
            text, warn = L["state_off"], False
        elif self.lan:
            info = s.connection_info(new_code=False)
            text, warn = texts.tpl_web("state_lan", url=info.get("plain") or s.local_url()), True
        else:
            text, warn = texts.tpl_web("state_local", url=s.local_url()), False
        return {"enabled": self.enabled, "lan": self.lan, "port": self.port, "text": text, "warn": warn,
                "lan_reason": None if self.enabled else L["state_off"],
                "connect_reason": None if self.lan else L["lan_off"]}

    # ---- 操作
    def _service(self):
        if self.service is None:
            from ..api.web.service import WebService

            self.service = WebService(self.win.api, port=self.port)
            self._unsub = self.service.on_change(self._bridge.changed.emit)
        return self.service

    def set_enabled(self, on: bool) -> None:
        self.win.prefs.set("web_enabled", bool(on))
        if on:
            self._service().start()
        elif self.service is not None:
            self.service.stop()
            if self.service.lan:
                self.service.access.set_lan(False)
            self.win.prefs.set("web_lan", False)  # 閉じたら同じネットワークへも出していない
        self.refresh()

    def set_lan(self, on: bool) -> None:
        self.win.prefs.set("web_lan", bool(on))
        s = self._service()
        s.set_lan(bool(on))
        self.refresh()
        if on and s.running:
            self.show_connect()
        elif not on:
            for w in (self.connect_window, self.clients_window):
                if w is not None:
                    w.close()

    def set_port(self, port: int) -> None:
        self.port = int(port)
        self.win.prefs.set("web_port", self.port)
        if self.service is not None:
            self.service.set_port(self.port)
        self.refresh()

    def regenerate(self) -> None:
        if self.service is None:
            return
        self.service.regenerate()
        if self.lan:
            self.show_connect()

    def disconnect(self, cid: str) -> None:
        if self.service is not None:
            self.service.access.disconnect(cid)

    def show_connect(self) -> None:
        if not self.lan:
            self.win.status_note(texts.WEB_LABELS["lan_off"])
            return
        if self.connect_window is not None:
            self.connect_window.close()
        # 開くたびに新しい認証番号（A §4.3.2 の 2）
        self.connect_window = ConnectWindow(self, self.service.connection_info(new_code=True))
        self.connect_window.show()
        self.connect_window.raise_()

    def show_clients(self) -> None:
        if self.clients_window is None:
            self.clients_window = ClientsWindow(self)
            self.clients_window.finished.connect(lambda _r: setattr(self, "clients_window", None))
        self.clients_window.refresh()
        self.clients_window.show()
        self.clients_window.raise_()

    def shutdown(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        if self.service is not None:
            self.service.stop()

    # ---- 画面へ映す
    def status_text(self) -> str:
        if not self.lan:
            return ""
        c = self.service.access.counts()
        return texts.tpl_web("status", n=c["total"], a=c[KIND_OPERATE], b=c[KIND_VIEW])

    def refresh(self) -> None:
        btn = getattr(self.win.status, "web", None)
        if btn is not None:
            t = self.status_text()
            btn.setText(t)
            btn.setVisible(bool(t))
        act = (getattr(self.win, "actions", None) or {}).get("web_connect")
        if act is not None:
            menus.set_reason(act, self.view()["connect_reason"])
        dlg = getattr(self.win, "_settings", None)
        if dlg is not None and hasattr(dlg, "apply_web"):
            dlg.apply_web()
        if self.clients_window is not None:
            self.clients_window.refresh()


def _truthy(v) -> bool:
    return v is True or (isinstance(v, str) and v.lower() in ("true", "1"))


class ConnectWindow(QDialog):
    """接続のしかた（QR 2つ・URL と［コピー］・6桁の認証番号と残りの秒数）。"""

    def __init__(self, ctl: WebControl, info: dict) -> None:
        super().__init__(ctl.win)
        L = texts.WEB_LABELS
        self.ctl = ctl
        self.info = info
        self.setWindowTitle(L["connect_title"])
        lay = QVBoxLayout(self)
        if not info.get("address"):
            lab = QLabel(L["no_address"])
            lab.setObjectName("warn")
            lab.setWordWrap(True)
            lay.addWidget(lab)
        else:
            grid = QGridLayout()
            for col, (kind, key) in enumerate(((KIND_OPERATE, "connect_operate"), (KIND_VIEW, "connect_view"))):
                url = info["urls"][kind]
                title = QLabel(L[key])
                f = title.font()
                f.setPointSizeF(f.pointSizeF() * 1.3)
                f.setBold(True)
                title.setFont(f)
                grid.addWidget(title, 0, col, alignment=Qt.AlignmentFlag.AlignHCenter)
                img = QLabel()
                img.setPixmap(qr_pixmap(url))
                img.setAlignment(Qt.AlignmentFlag.AlignCenter)
                grid.addWidget(img, 1, col)
                row = QHBoxLayout()
                edit = QLineEdit(url)
                edit.setReadOnly(True)
                edit.setCursorPosition(0)
                copy = QPushButton(L["copy"])
                copy.setObjectName("small")
                copy.clicked.connect(lambda _=False, u=url: self._copy(u))
                row.addWidget(edit, 1)
                row.addWidget(copy)
                box = QWidget()
                box.setLayout(row)
                grid.addWidget(box, 2, col)
            lay.addLayout(grid)
            note = QLabel(L["connect_qr_note"])
            note.setObjectName("dim")
            note.setWordWrap(True)
            lay.addWidget(note)
            how = QLabel(texts.tpl_web("code_label", plain=info.get("plain") or ""))
            how.setWordWrap(True)
            lay.addWidget(how)
            self.code = QLabel(self._spaced(info.get("code") or ""))
            f = QFont(self.code.font())
            f.setPointSizeF(f.pointSizeF() * 3)
            f.setBold(True)
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 6)
            self.code.setFont(f)
            self.code.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.code.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lay.addWidget(self.code)
            self.left = QLabel("")
            self.left.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(self.left)
            self.copied = QLabel("")
            self.copied.setObjectName("dim")
            lay.addWidget(self.copied)
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._tick)
            self._timer.start(1000)
            self._tick()
        warn = QLabel(L["note"])
        warn.setObjectName("dim")
        warn.setWordWrap(True)
        lay.addWidget(warn)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.button(QDialogButtonBox.StandardButton.Close).setText(L["close"])
        btns.rejected.connect(self.close)
        lay.addWidget(btns)

    @staticmethod
    def _spaced(code: str) -> str:
        return f"{code[:3]} {code[3:]}" if len(code) == 6 else code

    def _tick(self) -> None:
        cur = self.ctl.service.access.current_code() if self.ctl.service else None
        L = texts.WEB_LABELS
        if cur is None or cur[0] != self.info.get("code"):
            # 2 分を過ぎた・ほかで新しい番号を出した: この窓の番号はもう使えない
            self.left.setText(L["code_expired"])
            self.left.setObjectName("warn")
            self.code.setEnabled(False)
            self._timer.stop()
        else:
            self.left.setText(texts.tpl_web("code_valid", s=int(cur[1])))
        self.left.style().unpolish(self.left)
        self.left.style().polish(self.left)

    def _copy(self, url: str) -> None:
        QGuiApplication.clipboard().setText(url)
        self.copied.setText(texts.WEB_LABELS["copied"])


class ClientsWindow(QDialog):
    """Web でつながっている相手（つないだ時刻・IP アドレス・種類・［切断］）と［認証を作り直す］。"""

    COLS = ("col_time", "col_ip", "col_kind", "")

    def __init__(self, ctl: WebControl) -> None:
        super().__init__(ctl.win)
        L = texts.WEB_LABELS
        self.ctl = ctl
        self.setWindowTitle(L["clients_title"])
        lay = QVBoxLayout(self)
        self.state = QLabel("")
        lay.addWidget(self.state)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels([L[c] if c else "" for c in self.COLS])
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table, 1)
        self.empty = QLabel(L["clients_empty"])
        self.empty.setObjectName("dim")
        lay.addWidget(self.empty)
        row = QHBoxLayout()
        regen = QPushButton(L["regenerate"])
        regen.setToolTip(L["regenerate_tip"])
        regen.clicked.connect(ctl.regenerate)
        show = QPushButton(L["show_connect"])
        show.clicked.connect(ctl.show_connect)
        row.addWidget(regen)
        row.addWidget(show)
        row.addStretch(1)
        close = QPushButton(L["close"])
        close.clicked.connect(self.close)
        row.addWidget(close)
        lay.addLayout(row)
        self.resize(520, 300)

    def refresh(self) -> None:
        s = self.ctl.service
        conns = s.access.conns() if s is not None and self.ctl.lan else []
        self.state.setText(self.ctl.status_text() or texts.WEB_LABELS["lan_off"])
        self.table.setRowCount(len(conns))
        for i, c in enumerate(conns):
            t = datetime.datetime.fromtimestamp(c.connected_at).strftime("%H:%M:%S")
            for j, v in enumerate((t, c.ip, texts.WEB_KINDS.get(c.kind, c.kind))):
                it = QTableWidgetItem(v)
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(i, j, it)
            b = QPushButton(texts.WEB_LABELS["disconnect"])
            b.setObjectName("small")
            b.clicked.connect(lambda _=False, cid=c.conn_id: self.ctl.disconnect(cid))
            self.table.setCellWidget(i, 3, b)
        self.empty.setVisible(not conns)
