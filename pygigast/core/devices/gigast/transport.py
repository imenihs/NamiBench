"""GigaSt とのシリアルの送受信（設計B §3・§7.2・§7.3）。

- 1,000,000bps・8N1・フロー制御なし。読み取りの待ちは短く（20ms）して、繰り返しの中で3つの上限時間を見る
- 送る前に必ず受信バッファを捨てる（T6: 掃引の途中に要求が届くと、後で余計な1バイトが来る）
- 受け取った掃引は、バイト数がちょうど Plot×2+4 のときだけ使う。末尾4バイトの中身は判定に使わない
- pyserial の例外（読み書きの失敗・ポートが消えた）は DeviceDisconnected にする
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional, Protocol

import serial
import serial.tools.list_ports

from ...types import DeviceDisconnected, PortInUse, SweepDiscarded
from .protocol import Limits
from .traffic_log import TrafficLog

BAUDRATE = 1_000_000
READ_TIMEOUT_S = 0.02
WRITE_TIMEOUT_S = 1.0
RX_BUFFER = 65536  # 最大の応答（12500点で 25004 バイト）より大きく取る

# FTDI FT232RL の USB ID（設計B §2 の3）。汎用の部品なので、これだけでは GigaSt と決められない（設計B §7.3）
FTDI_VID = 0x0403
FTDI_PID = 0x6001


@dataclass(frozen=True)
class PortInfo:
    """見つかった FTDI のポート。利用者に選ばせるときはシリアル番号とポート名を並べる。"""

    port: str
    serial: str
    description: str = ""


def discover() -> list[PortInfo]:
    """USB ID が FTDI 0403:6001 のポートを一覧にする（設計B §7.3）。"""
    found = []
    for p in serial.tools.list_ports.comports():
        if p.vid == FTDI_VID and p.pid == FTDI_PID:
            found.append(PortInfo(port=p.device, serial=p.serial_number or "", description=p.description or ""))
    return sorted(found, key=lambda x: (x.serial, x.port))


def find_by_serial(serial_number: str) -> Optional[PortInfo]:
    """覚えたシリアル番号の機器を探す（COM 番号が変わっても同じ機器を見つける）。"""
    for p in discover():
        if p.serial == serial_number:
            return p
    return None


class PortLike(Protocol):
    """送受信に使うポートの形（pyserial の Serial と、試験・シミュレータの偽のポート）。"""

    in_waiting: int  # 届いていて、まだ読んでいないバイト数

    def reset_input_buffer(self) -> None: ...
    def write(self, data: bytes) -> Optional[int]: ...
    def read(self, size: int = 1) -> bytes: ...
    def close(self) -> None: ...


# ポートがほかのアプリに開かれているときの OS の失敗（Windows はアクセス拒否、POSIX は使用中・排他のロック）
_IN_USE_ERRNO = {13, 16}
_IN_USE_TEXT = ("PermissionError", "Access is denied", "アクセスが拒否", "exclusively lock", "Device or resource busy")


def is_port_in_use(e: BaseException) -> bool:
    """開けなかった理由が「ほかのアプリが使っている」か（設計J §4.7: 確認の掃引の失敗と分けて知らせる）。

    pyserial は OS の失敗を SerialException の文に入れて返す（Windows: "PermissionError(13, ...)"）ので、
    errno と文の両方を見る。
    """
    for x in (e, e.__cause__, e.__context__):
        if x is None:
            continue
        if isinstance(x, PermissionError) or getattr(x, "errno", None) in _IN_USE_ERRNO:
            return True
        if any(t in str(x) for t in _IN_USE_TEXT):
            return True
    return False


def open_serial(port: str) -> serial.Serial:
    """ポートを開く。ほかのアプリが使っていれば PortInUse、それ以外で開けなければ DeviceDisconnected。"""
    try:
        s = serial.Serial(
            port=port,
            baudrate=BAUDRATE,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=READ_TIMEOUT_S,
            write_timeout=WRITE_TIMEOUT_S,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        if hasattr(s, "set_buffer_size"):  # Windows だけにある
            s.set_buffer_size(rx_size=RX_BUFFER, tx_size=4096)
        return s
    except (serial.SerialException, OSError, ValueError) as e:
        if is_port_in_use(e):
            raise PortInUse(f"ほかのアプリが使用中: {e}") from e
        raise DeviceDisconnected(f"ポートを開けない: {e}") from e


@dataclass
class Exchange:
    """1回の要求の結果（ちょうどのバイト数を受け取ったとき）。"""

    response: bytes  # 末尾4バイトを含む生の応答
    duration_s: float
    first_byte_s: float


class Transport:
    """1本のポートでの要求と応答。掃引の途中で次の要求を送らないのは呼ぶ側（1本のスレッド）が守る。"""

    def __init__(
        self,
        port: PortLike,
        traffic_log: Optional[TrafficLog] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.port = port
        self.traffic_log = traffic_log
        self._clock = clock
        # いまの要求の見積もりの終わり（clock の時刻）。残り時間を出すのに使う（設計B §7.2 の停止）
        self.busy_until: Optional[float] = None

    def exchange(self, request: bytes, expected: int, lim: Limits, note: Optional[str] = None) -> Exchange:
        """受信バッファを捨て、32バイトを送り、ちょうど expected バイトを受け取る。

        - 上限時間を超えたら受信バッファを捨てて SweepDiscarded（reason は上限の名前）
        - 読み書きの例外は DeviceDisconnected
        """
        buf = bytearray()
        first: Optional[float] = None
        t0 = self._clock()
        try:
            self.port.reset_input_buffer()
            self.port.write(request)
            t0 = self._clock()
            self.busy_until = t0 + lim.estimated_s
            last = t0
            fired: Optional[str] = None
            while len(buf) < expected:
                # 届いている分だけ読み、無ければ1バイトを短い待ち（READ_TIMEOUT_S）で待つ。
                # 残り全部を read(n) で待つ形より、上限時間を見る間隔がドライバの待ち方に左右されない
                want = min(expected - len(buf), max(1, getattr(self.port, "in_waiting", 0) or 0))
                chunk = self.port.read(want)
                now = self._clock()
                if chunk:
                    if first is None:
                        first = now - t0
                    buf += chunk
                    last = now
                    if len(buf) >= expected:
                        break
                if first is None and now - t0 > lim.first_byte_s:
                    fired = "first_byte"
                elif first is not None and now - last > lim.silence_s:
                    fired = "silence"
                elif now - t0 > lim.total_s:
                    fired = "total"
                if fired:
                    break
            duration = self._clock() - t0
            if fired:
                # 打ち切ったら受信バッファを捨てる（次の要求の前にも捨てる。T7: これで戻る）
                self.port.reset_input_buffer()
                self._log(request, expected, bytes(buf), duration, first, fired, None, note)
                raise SweepDiscarded(fired, {"expected": expected, "received": len(buf), "duration_s": duration})
        except (serial.SerialException, OSError) as e:
            duration = self._clock() - t0
            self._log(request, expected, bytes(buf), duration, first, None, f"{type(e).__name__}: {e}", note)
            self._close_quietly()
            raise DeviceDisconnected(str(e)) from e
        finally:
            self.busy_until = None
        self._log(request, expected, bytes(buf), duration, first, None, None, note)
        return Exchange(response=bytes(buf), duration_s=duration, first_byte_s=first or 0.0)

    def _log(self, request, expected, response, duration, first, limit, error, note) -> None:
        if self.traffic_log is not None:
            self.traffic_log.record(request, expected, response, duration, first, limit, error, note)

    def _close_quietly(self) -> None:
        try:
            self.port.close()
        except Exception:  # 切れたポートを閉じるときの例外は、切断の知らせより優先しない
            pass

    def close(self) -> None:
        self._close_quietly()
