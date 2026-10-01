"""Web の口を LAN に開くときの認証と接続の一覧（設計A §4.3.2）。

- 鍵は起動のたびに作る2つ（操作できる・見るだけ）。URL（`#k=<鍵>`）と QR の中にだけ出す
- 手で打つ用の6桁の認証番号は 2 分で無効。新しい番号を出すと前の番号は無効（出ている番号は1つ）
- 鍵か番号を出したブラウザには、ブラウザごとの乱数の札（セッション）をクッキーで渡す。
  札を鍵そのものにしないのは、一覧の行ごとの［切断］でそのブラウザだけを入れなくするため（設計に無い判断）。
  札は「認証を作り直す」と LAN を OFF にしたときに全部無効にする
- 認証番号を 5 回続けて間違えた接続元（IP アドレス）は 1 分間、入力を受け付けない
- 自分の PC（ループバック）からは認証なしで操作できる（A §4.3.2 の 8）
- Host と Origin の確かめ（A §4.3.2 の 9）: Host は 127.0.0.1・localhost・::1 か、要求が届いた自分のアドレス
  （LAN に開いているときだけ）とポート。Origin があれば `http://<Host>` と同じでなければ断る

ここは画面も通信も持たない（スレッドの間で共有するので錠で守る）。変わったら on_change の関数を呼ぶ。
"""
from __future__ import annotations

import ipaddress
import secrets
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

KIND_OPERATE = "operate"
KIND_VIEW = "view"
KINDS = (KIND_OPERATE, KIND_VIEW)

CODE_VALID_S = 120.0  # 認証番号の有効な時間（2 分）
MAX_WRONG = 5  # 続けて間違えてよい回数
BLOCK_S = 60.0  # 間違えたときに止める時間（1 分）
KEY_BYTES = 24  # 鍵の長さ（バイト。URL に入れるときは 32 文字）
SESSION_BYTES = 24

COOKIE_PREFIX = "pygigast_s_"  # クッキーの名前（ポートごとに分ける。クッキーはポートを区別しないため）

_LOCAL_NAMES = {"127.0.0.1", "localhost", "::1"}


def cookie_name(port: int) -> str:
    return f"{COOKIE_PREFIX}{int(port)}"


def is_loopback(ip: Optional[str]) -> bool:
    if not ip:
        return False
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return ip == "localhost"
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped is not None:
        a = a.ipv4_mapped
    return a.is_loopback


def split_host(host: str) -> tuple[str, Optional[int]]:
    """Host の値を名前とポートに分ける（[::1]:8080 の形も）。ポートが無ければ None。"""
    host = host.strip()
    if host.startswith("["):
        end = host.find("]")
        if end < 0:
            return host, None
        name = host[1:end]
        rest = host[end + 1:]
        if rest.startswith(":") and rest[1:].isdigit():
            return name, int(rest[1:])
        return name, None if not rest else -1
    if host.count(":") == 1:
        name, _, p = host.partition(":")
        return name, int(p) if p.isdigit() else -1
    return host, None


def host_allowed(host: Optional[str], server: Optional[tuple], lan: bool) -> bool:
    """Host が自分の宛先か（DNS のリバインディングを防ぐ: A §4.3.2 の 9）。

    server は要求が届いた自分のソケットの (アドレス, ポート)。LAN に開いているときは、届いたアドレス
    （= この PC の LAN のアドレス）も自分の宛先にする。
    """
    if not host or not server:
        return False
    name, port = split_host(host)
    sport = int(server[1])
    if port is None:
        port = 80
    if port != sport:
        return False
    name = name.lower()
    if name in _LOCAL_NAMES:
        return True
    sip = str(server[0])
    if name == sip.lower():
        return lan or is_loopback(sip)
    return False


def origin_allowed(origin: Optional[str], host: Optional[str]) -> bool:
    """Origin が無ければ通す（ブラウザ以外の道具）。あれば Host と同じ宛先でなければ断る。"""
    if origin is None:
        return True
    return bool(host) and origin.lower() == f"http://{host}".lower()


def lan_addresses() -> list[str]:
    """この PC の LAN の IPv4 アドレス（QR と URL に使う）。外へ繋がる経路のアドレスを先頭にする。

    UDP のソケットを外の宛先へ connect するだけで、何も送らない（経路から自分のアドレスを知る一般的な方法）。
    """
    out: list[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))  # 文書用のアドレス（届かなくてよい）
            out.append(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in out:
                out.append(ip)
    except OSError:
        pass
    return [ip for ip in out if not is_loopback(ip) and not ip.startswith("169.254.") and ip != "0.0.0.0"]


@dataclass
class Session:
    token: str
    kind: str
    ip: str
    created_at: float


@dataclass
class Conn:
    """WebSocket で繋いでいる画面1つ（一覧の1行）。"""

    conn_id: str
    kind: str
    ip: str
    connected_at: float
    session: Optional[str]
    local: bool
    close: Callable[[], None] = field(repr=False, default=lambda: None)


class Access:
    def __init__(self, lan: bool = False, now: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time) -> None:
        self._lock = threading.RLock()
        self._now = now
        self._wall = wall
        self.lan = bool(lan)
        self.keys: dict[str, str] = {}
        self._sessions: dict[str, Session] = {}
        self._code: Optional[tuple[str, float]] = None  # (番号, 無効になる時刻)
        self._wrong: dict[str, int] = {}
        self._blocked: dict[str, float] = {}
        self._conns: dict[str, Conn] = {}
        self._listeners: list[Callable[[], None]] = []
        self.regenerate(notify=False)

    # ---- 知らせ
    def on_change(self, fn: Callable[[], None]) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(fn)

        def off() -> None:
            with self._lock:
                if fn in self._listeners:
                    self._listeners.remove(fn)

        return off

    def _changed(self) -> None:
        for fn in list(self._listeners):
            try:
                fn()
            except Exception:  # noqa: BLE001 — 画面の側の失敗で認証を止めない
                pass

    # ---- 鍵・番号
    def regenerate(self, notify: bool = True) -> None:
        """鍵を作り直し、覚えていた全部のブラウザを入れなくして切る（A §4.3.2 の 5）。新しい番号も出す。"""
        with self._lock:
            self.keys = {k: secrets.token_urlsafe(KEY_BYTES) for k in KINDS}
            self._sessions.clear()
            victims = [c for c in self._conns.values() if not c.local]
            self._code = None
            self.new_code()
        for c in victims:
            c.close()
        if notify:
            self._changed()

    def new_code(self) -> str:
        """新しい6桁の認証番号（前の番号は無効になる）。"""
        with self._lock:
            code = f"{secrets.randbelow(1_000_000):06d}"
            self._code = (code, self._now() + CODE_VALID_S)
            return code

    def current_code(self) -> Optional[tuple[str, float]]:
        """(番号, 残りの秒数)。無効なら None。"""
        with self._lock:
            if self._code is None:
                return None
            code, until = self._code
            left = until - self._now()
            return (code, left) if left > 0 else None

    def set_lan(self, on: bool) -> None:
        with self._lock:
            self.lan = bool(on)
            if not on:
                # LAN を OFF にしたら覚えていたブラウザも入れなくする（自分の PC だけに戻す: A §4.3.2 の 6）
                self._sessions.clear()
        self._changed()

    # ---- 入る
    def blocked_for(self, ip: str) -> float:
        """その接続元が止められている残りの秒数（0 なら止まっていない）。"""
        with self._lock:
            until = self._blocked.get(ip)
            if until is None:
                return 0.0
            left = until - self._now()
            if left <= 0:
                del self._blocked[ip]
                return 0.0
            return left

    def login_key(self, key: str, ip: str) -> Optional[Session]:
        """URL の鍵で入る。合わなければ None（鍵は長い乱数なので回数では止めない）。"""
        with self._lock:
            for kind, k in self.keys.items():
                if isinstance(key, str) and secrets.compare_digest(key, k):
                    return self._new_session(kind, ip)
        return None

    def login_code(self, code: str, ip: str) -> tuple[Optional[Session], str]:
        """認証番号で入る。(札 or None, 結果のコード: ok / wrong_code / code_expired / blocked)。

        番号で入ると操作できる接続になる（番号は PC の前の人が見せるもの。設計に無い判断）。
        """
        with self._lock:
            if self.blocked_for(ip) > 0:
                return None, "blocked"
            cur = self.current_code()
            ok = cur is not None and isinstance(code, str) and secrets.compare_digest(code.strip(), cur[0])
            if ok:
                self._wrong.pop(ip, None)
                return self._new_session(KIND_OPERATE, ip), "ok"
            n = self._wrong.get(ip, 0) + 1
            if n >= MAX_WRONG:
                self._wrong.pop(ip, None)
                self._blocked[ip] = self._now() + BLOCK_S
                return None, "blocked"
            self._wrong[ip] = n
            return None, "wrong_code"

    def _new_session(self, kind: str, ip: str) -> Session:
        s = Session(secrets.token_urlsafe(SESSION_BYTES), kind, ip, self._wall())
        self._sessions[s.token] = s
        return s

    def session(self, token: Optional[str]) -> Optional[Session]:
        if not token:
            return None
        with self._lock:
            return self._sessions.get(token)

    # ---- 繋いでいる画面
    def add_conn(self, kind: str, ip: str, session: Optional[str], local: bool,
                 close: Callable[[], None]) -> str:
        with self._lock:
            cid = secrets.token_hex(6)
            self._conns[cid] = Conn(cid, kind, ip, self._wall(), session, local, close)
        self._changed()
        return cid

    def remove_conn(self, cid: str) -> None:
        with self._lock:
            had = self._conns.pop(cid, None) is not None
        if had:
            self._changed()

    def conns(self, include_local: bool = False) -> list[Conn]:
        with self._lock:
            return sorted((c for c in self._conns.values() if include_local or not c.local),
                          key=lambda c: c.connected_at)

    def disconnect(self, cid: str) -> bool:
        """一覧の1行を切る。そのブラウザの札も無効にする（繋ぎ直しても入れない）。"""
        with self._lock:
            c = self._conns.get(cid)
            if c is None:
                return False
            victims = [c]
            if c.session:
                self._sessions.pop(c.session, None)
                victims = [x for x in self._conns.values() if x.session == c.session]
        for v in victims:
            v.close()
        self._changed()
        return True

    def disconnect_all(self) -> None:
        with self._lock:
            victims = list(self._conns.values())
        for v in victims:
            v.close()

    def counts(self) -> dict:
        """状態バーの数（LAN の接続だけ）。"""
        cs = self.conns()
        return {"total": len(cs), KIND_OPERATE: sum(1 for c in cs if c.kind == KIND_OPERATE),
                KIND_VIEW: sum(1 for c in cs if c.kind == KIND_VIEW)}


def urls(port: int, keys: dict[str, str], addresses: Optional[list[str]] = None) -> dict:
    """QR と URL（`http://<この PC のアドレス>:<ポート>/#k=<鍵>`）。アドレスが無ければ空。"""
    addrs = lan_addresses() if addresses is None else addresses
    if not addrs:
        return {"address": None, "plain": None, KIND_OPERATE: None, KIND_VIEW: None, "addresses": []}
    base = f"http://{addrs[0]}:{int(port)}/"
    return {"address": addrs[0], "plain": base, "addresses": addrs,
            **{k: f"{base}#k={keys[k]}" for k in KINDS}}
