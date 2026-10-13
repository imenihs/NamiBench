"""PlutoSDR を見つける（設計Q §7.2）。USB で見える機器と、設定の URI で繋がる機器をシリアル番号で1行にまとめる。

機器の口（driver.py）と責務が違う（一覧は 0.5s ごとに作り、機器を開かずに済ませる）ので分けてある。driver.discover が
この discover を、問い合わせ直さないための入れ物（driver._cache: 試験が作り直す）を渡して呼ぶ。
"""
from __future__ import annotations

import re
import socket
import time
from dataclasses import dataclass
from typing import Optional

from ...types import DeviceError

DEFAULT_URI = "ip:192.168.2.1"
IIOD_PORT = 30431
# ip の URI に繋がるかを確かめる時間の上限（一覧を作るたびに待つので短く）
PROBE_TIMEOUT_S = 0.3
UNREACHABLE_CACHE_S = 3.0
# 繋がっている ip の機器のシリアル番号を問い合わせ直す間隔（一覧は 0.5s ごとに作るので、毎回は文脈を作らない）
SERIAL_CACHE_S = 5.0
_SERIAL_RE = re.compile(r"serial=([0-9A-Za-z]+)")


@dataclass(frozen=True)
class PlutoPortInfo:
    port: str  # URI
    serial: str
    description: str
    available: bool = True


class _SerialCache:
    """ip の URI → (シリアル番号, 説明, 問い合わせた時刻)。"""

    def __init__(self) -> None:
        self._d: dict[str, tuple[str, str, float]] = {}

    def get(self, uri: str) -> Optional[tuple[str, str]]:
        v = self._d.get(uri)
        if v is None or time.monotonic() - v[2] > SERIAL_CACHE_S:
            return None
        return v[0], v[1]

    def put(self, uri: str, serial: str, desc: str) -> None:
        self._d[uri] = (serial, desc, time.monotonic())

    def drop(self, uri: str) -> None:
        self._d.pop(uri, None)


_unreachable_until: dict[str, float] = {}


def _reachable(uri: str) -> bool:
    """ip の URI の iiod に TCP で繋がるか（繋がらない宛先で文脈を作ると長く待つため、先に短く確かめる）。

    繋がらなかった宛先は UNREACHABLE_CACHE_S の間は確かめ直さない（一覧は 0.5s ごとに作るので、時間切れまで待つ
    確かめを毎回しない）。
    """
    if time.monotonic() < _unreachable_until.get(uri, 0.0):
        return False
    host = uri[3:]
    try:
        with socket.create_connection((host, IIOD_PORT), timeout=PROBE_TIMEOUT_S):
            _unreachable_until.pop(uri, None)
            return True
    except OSError:
        _unreachable_until[uri] = time.monotonic() + UNREACHABLE_CACHE_S
        return False


def _ask_serial(lib, uri: str) -> Optional[tuple[str, str]]:
    try:
        ctx = lib.open(uri, 2000)
    except DeviceError:
        return None
    try:
        return (ctx.attr("hw_serial") or ""), (ctx.attr("hw_model") or "PlutoSDR")
    finally:
        ctx.close()


def discover(lib, uris, cache: _SerialCache) -> list[PlutoPortInfo]:
    """USB で見える PlutoSDR と、設定の URI で繋がる PlutoSDR を、シリアル番号で1行にまとめて並べる。

    同じ機器が USB と ip の両方で見えるときは、設定の URI の行だけを出す（設計Q §7.2: URI は設定で変えられる）。
    """
    reach = getattr(lib, "reachable", None) or _reachable
    rows: dict[str, PlutoPortInfo] = {}
    order: list[str] = []
    for uri in uris:
        if not uri:
            continue
        info: Optional[tuple[str, str]]
        if uri.startswith("ip:"):
            if not reach(uri):
                cache.drop(uri)
                continue
            info = cache.get(uri) or _ask_serial(lib, uri)
        else:
            info = _ask_serial(lib, uri)
        if info is None:
            continue
        cache.put(uri, info[0], info[1])
        key = info[0] or uri
        if key not in rows:
            rows[key] = PlutoPortInfo(port=uri, serial=info[0], description=info[1])
            order.append(key)
    try:
        found = lib.scan("usb")
    except Exception:  # noqa: BLE001 — USB の一覧が作れなくても ip の行は出す
        found = []
    for uri, desc in found:
        m = _SERIAL_RE.search(desc or "")
        serial = m.group(1) if m else ""
        key = serial or uri
        if key in rows:
            continue
        name = "PlutoSDR"
        mm = re.search(r"\(([^()]*PlutoSDR[^()]*)\)", desc or "")
        if mm:
            name = mm.group(1)
        rows[key] = PlutoPortInfo(port=uri, serial=serial, description=name)
        order.append(key)
    return [rows[k] for k in order]
