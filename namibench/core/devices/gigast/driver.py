"""GigaSt v5 の機器の口（設計B §7.1〜§7.3）。

measure() の中で、前置きの切り落とし・イメージ除去の区間ごとの回と合わせ方（設計F §7）・NF の HOT/COLD の組を行う。
機器ごとの癖はここに閉じ、外へは生の AD 値（uint16）と、それをどう読むかの encoding だけを返す。

制御のスレッドへの組み込み（切れたら状態を出して再接続を待つ）は呼ぶ側が行う。そのための部品として
Reconnector（ポート一覧を 0.5s ごとに見て、同じシリアル番号が戻るのを待つ）を置く。
"""
from __future__ import annotations

import copy
import threading
import time
from typing import Callable, Optional

import numpy as np

from ...types import (
    AdoptedSweep,
    DeviceDisconnected,
    DeviceError,
    DeviceIdentity,
    InvalidSweep,
    Mode,
    PortInUse,
    RawSegment,
    RawSweep,
    SweepDiscarded,
)
from ..base import Device
from . import image_reject, protocol
from .protocol import DeviceSweep, Limits
from .traffic_log import TrafficLog
from .transport import Exchange, PortInfo, Transport, discover, find_by_serial, open_serial

ENCODING = "gigast_ad_u16"
RECONNECT_POLL_S = 0.5  # 設計B §7.3（T8: 抜けると約0.2s 後に一覧から消える）

# 確認の掃引（設計B §7.3）。Plot 500 の短いもの。これまでの実機試験の健全性確認と同じ条件
PROBE_START_HZ = 495_000_000
PROBE_STEP_HZ = 20_000
PROBE_POINTS = 500
PROBE_RBW_HZ = 180_000
PROBE_MODE_BITS = protocol.MODE_B3  # SA・イメージ除去なしと同じ b3=1（信号源は出さない）


def timing_fn(profile: dict) -> Callable[..., tuple[float, float]]:
    """掃引条件モデルが使う見積もりの関数 (step_hz, rbw_hz, mode) -> (初バイトまで[s], 1点あたり[s])。"""
    return protocol.timing_fn(profile)


def _requests(sweep: AdoptedSweep) -> list[tuple[str, Optional[int], int, int]]:
    """1回の measure で送る要求の並び (名前, 区間の番号, b3, b2)。

    - NF: b2 を 1/0（HOT/COLD）。b3 は 1（イメージ除去は使わない）。軸の全部を1つの要求で送る
    - それ以外: 掃引条件モデルが決めた区間ごとに、区間の b3 の並びの回数分を続けて送る（設計F §7.2: 区間 1 を
      N1 回 → 区間 2 を N2 回…）。なしは b3=1 の1回（旧アプリの校正ファイルのレベルに合う: 設計B §7.2）
    名前は「s<区間>_b3_<b3><同じ b3 の何回目 a・b>」（送受信の記録と捨てた知らせに出る）。
    """
    if sweep.mode == Mode.NF:
        return [("hot", None, 1, 1), ("cold", None, 1, 0)]
    if not sweep.segments:
        raise InvalidSweep("送る区間が無い（掃引条件モデルを通していない条件）")
    out: list[tuple[str, Optional[int], int, int]] = []
    for k, seg in enumerate(sweep.segments):
        seen: dict[int, int] = {}
        for b3 in seg.b3:
            j = seen.get(b3, 0)
            seen[b3] = j + 1
            out.append((f"s{k}_b3_{b3}{chr(ord('a') + j)}", k, b3, 0))
    return out


def _cancelled(cancel: Optional[threading.Event]) -> bool:
    return cancel is not None and cancel.is_set()


class SweepRunner:
    """要求の組み立て・複数回の掃引・前置きの切り落とし（実機とシミュレータで共通）。

    実装は _exchange（32バイトを送り、ちょうどのバイト数を受け取る）と _profile を持ち、Device と合わせて使う
    （進み具合は Device.report_progress で知らせる）。
    """

    _profile: dict

    def _exchange(self, request: bytes, expected: int, lim: Limits, note: Optional[str],
                  on_received: Optional[Callable[[int], None]] = None) -> Exchange:
        raise NotImplementedError

    def _limits(self, ds: DeviceSweep) -> Limits:
        return protocol.limits(self._profile, ds.step_hz, ds.rbw_hz, ds.points, mode=protocol.timing_mode(ds))

    def measure(self, sweep: AdoptedSweep, cancel: Optional[threading.Event] = None) -> RawSweep:
        """1回測って生の値を返す（base.Device.measure の約束どおり）。

        - 送る前に全部の要求を検証する（範囲外は1つも送らずに InvalidSweep）
        - cancel が立っていたら次の要求を送らない。送った要求は終わりまで受けてから cancelled で捨てる
        - 1画面の要求（イメージ除去の全部の区間の回・NF の2回）のどれかが捨てられたら画面ごと捨てる（設計F §7.2）
        - 進み具合は、1画面の全部の要求の点の数（前置きを含む）を全体とし、届いた点の数で知らせる（設計J §4.13）
        """
        plan = []
        for name, k, b3, b2 in _requests(sweep):
            seg = None if k is None else sweep.segments[k]
            plan.append((name, k, protocol.device_sweep(sweep, self._profile, b3, b2, seg)))
        if _cancelled(cancel):
            raise SweepDiscarded("cancelled", {"sent": 0})
        total = sum(ds.points for _name, _k, ds in plan)
        self.report_progress(0, total)
        done = 0  # 前の要求までに届いた点の数
        started_at = time.time()
        t0 = time.monotonic()
        got: dict[str, np.ndarray] = {}
        for i, (name, k, ds) in enumerate(plan):
            if i > 0 and _cancelled(cancel):
                raise SweepDiscarded("cancelled", {"sent": i})

            def on_received(n: int, base: int = done, points: int = ds.points) -> None:
                # 届いたバイトを点に直す（1点 2 バイト。末尾4バイトは点ではない）
                self.report_progress(base + min(n // 2, points), total)

            try:
                ex = self._exchange(protocol.pack(ds), ds.expected_bytes, self._limits(ds), name, on_received)
            except SweepDiscarded as e:
                if len(plan) > 1:
                    e.detail.setdefault("pass", name)
                    e.detail.setdefault("pair_discarded", True)
                    e.detail.setdefault("passes", len(plan))
                raise
            done += ds.points
            data = np.frombuffer(ex.response, dtype="<u2", count=ds.points)
            # 前置きの点を切り落とす（設計C §5.6。区間ごとの前置き: 設計F §7.2）。uint16 のまま持つ（換算は機器の口の外: 設計D）
            pre = sweep.preroll_points if k is None else sweep.segments[k].preroll_points
            got[name] = data[pre:].astype(np.uint16)
        # 掃引は最後まで受けた。そのあとで取り消しが分かったら結果を捨てる（設計B §3）
        if _cancelled(cancel):
            raise SweepDiscarded("cancelled", {"sent": len(plan)})
        duration = time.monotonic() - t0
        first_ds = plan[0][2]
        extra = {
            "sample": first_ds.sample,
            "device_points": first_ds.points,
            "device_start_hz": first_ds.start_hz,
            "passes": len(plan),
        }
        common = dict(
            adopted=sweep,
            encoding=ENCODING,
            started_at=started_at,
            duration_s=duration,
            transient_head_points=sweep.transient_head_points,
            extra=extra,
        )
        if sweep.mode == Mode.NF:
            return RawSweep(raw=got["hot"], raw_hot=got["hot"], raw_cold=got["cold"], **common)
        # 区間ごとに回を合わせ、区間を並べて1画面にする（設計F §7.1・§7.2）。区間ごとの生の値はどれも返す（保存する: §7.3）
        parts: list[np.ndarray] = []
        segs: list[RawSegment] = []
        for k, seg in enumerate(sweep.segments):
            vals = np.stack([got[name] for name, kk, _ds in plan if kk == k])
            parts.append(image_reject.combine(vals, seg.b3, sweep.rbw_hz, self._profile))
            segs.append(RawSegment(point0=seg.point0, b3=tuple(seg.b3), values=vals))
        raw = np.concatenate(parts).astype(np.uint16)
        if all(s.passes == 1 for s in segs):
            return RawSweep(raw=raw, **common)  # なし: b3=1 の1回だけ（生の値はそのまま raw）
        return RawSweep(raw=raw, raw_segments=tuple(segs), **common)


class GigaStDevice(SweepRunner, Device):
    """実機の GigaSt v5。"""

    def __init__(self, profile: Optional[dict] = None, traffic_log: Optional[TrafficLog] = None) -> None:
        self._profile = copy.deepcopy(profile if profile is not None else protocol.default_profile())
        self._traffic_log = traffic_log
        self._transport: Optional[Transport] = None
        self._identity: Optional[DeviceIdentity] = None
        # 接続（＝ USB 給電の電源の入れ直し）の時刻（設計B §7.2.1）
        self.connected_at: Optional[float] = None

    # ---- 繋ぐ ----
    def open(self, serial: Optional[str] = None, port: Optional[str] = None) -> DeviceIdentity:
        """シリアル番号（覚えた機器）かポート名（利用者が一覧から選んだ機器）で開く。

        開くだけで何も送らない。初めて繋ぐ機器は、先に probe() で GigaSt かを確かめる（設計B §7.3）。
        """
        self.close()
        info: Optional[PortInfo]
        if serial is not None:
            info = find_by_serial(serial)
            if info is None:
                raise DeviceDisconnected(f"シリアル番号 {serial} の機器が見つからない")
        elif port is not None:
            info = next((p for p in discover() if p.port == port), PortInfo(port=port, serial=""))
        else:
            raise DeviceError("serial か port のどちらかを指定する（知らない機器には繋がない）")
        s = open_serial(info.port)
        self._transport = Transport(s, self._traffic_log)
        self._identity = DeviceIdentity(model=self._profile["model"], serial=info.serial, port=info.port)
        self.connected_at = time.time()
        return self._identity

    @staticmethod
    def probe(port: str, profile: Optional[dict] = None, traffic_log: Optional[TrafficLog] = None) -> bool:
        """確認の掃引を1回送り、上限時間内にちょうど 1004 バイトが返れば GigaSt と判断する（設計B §7.3）。

        末尾4バイトの値は使わない。ポートは自分で開いて閉じる（開いている機器とは別に使う）。
        """
        profile = profile if profile is not None else protocol.default_profile()
        ds = protocol.make_device_sweep(
            profile, PROBE_START_HZ, PROBE_STEP_HZ, PROBE_POINTS, PROBE_RBW_HZ, PROBE_MODE_BITS, None
        )
        try:
            s = open_serial(port)
        except PortInUse:
            raise  # ほかのアプリが使っている: 「GigaSt でない」とは別に知らせる（設計J §4.7）
        except DeviceDisconnected:
            return False
        t = Transport(s, traffic_log)
        try:
            t.exchange(protocol.pack(ds), ds.expected_bytes,
                       protocol.limits(profile, ds.step_hz, ds.rbw_hz, ds.points), "probe")
            return True
        except (SweepDiscarded, DeviceDisconnected):
            return False
        finally:
            t.close()

    # ---- 機器の口 ----
    def profile(self) -> dict:
        return self._profile

    def identity(self) -> DeviceIdentity:
        if self._identity is None:
            raise DeviceDisconnected("開いていない")
        return self._identity

    @property
    def is_open(self) -> bool:
        return self._transport is not None

    @property
    def busy_until(self) -> Optional[float]:
        """いまの要求の見積もりの終わり（time.monotonic の時刻）。停止待ちの残り時間の表示に使う。"""
        return None if self._transport is None else self._transport.busy_until

    def _exchange(self, request: bytes, expected: int, lim: Limits, note: Optional[str],
                  on_received: Optional[Callable[[int], None]] = None) -> Exchange:
        if self._transport is None:
            raise DeviceDisconnected("開いていない")
        try:
            return self._transport.exchange(request, expected, lim, note, on_received)
        except DeviceDisconnected:
            # 読み書きの例外でポートは閉じた。再接続は Reconnector で同じシリアル番号を待つ
            self._transport = None
            raise

    def close(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    # ---- 切れたら戻る（設計B §7.3）----
    def reconnect(self, stop: Optional[threading.Event] = None, timeout_s: Optional[float] = None) -> bool:
        """同じシリアル番号の機器が戻るのを待って開き直す。戻れば True、stop か timeout なら False。"""
        if self._identity is None or not self._identity.serial:
            raise DeviceError("シリアル番号が分からない機器は自動で繋ぎ直さない")
        info = Reconnector(self._identity.serial).wait(stop, timeout_s)
        if info is None:
            return False
        self.open(serial=self._identity.serial)
        return True


class Reconnector:
    """ポート一覧を一定の間隔で見て、同じシリアル番号の機器が戻るのを待つ（設計B §7.3・T8）。

    制御のスレッドから呼ぶ。待つ間も stop（利用者が待つのをやめた）で抜けられる。
    掃引していないときに抜けたことを知るには present() を同じ間隔で呼ぶ。
    """

    def __init__(
        self,
        serial: str,
        finder: Callable[[str], Optional[PortInfo]] = find_by_serial,
        interval_s: float = RECONNECT_POLL_S,
    ) -> None:
        self.serial = serial
        self._finder = finder
        self.interval_s = interval_s

    def present(self) -> Optional[PortInfo]:
        """いまポート一覧にあればその情報、無ければ None。"""
        return self._finder(self.serial)

    def wait(self, stop: Optional[threading.Event] = None, timeout_s: Optional[float] = None) -> Optional[PortInfo]:
        """戻ったらその情報を返す。stop が立つか timeout_s を過ぎたら None。"""
        stop = stop or threading.Event()
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        while not stop.is_set():
            info = self.present()
            if info is not None:
                return info
            if deadline is not None and time.monotonic() >= deadline:
                return None
            stop.wait(self.interval_s)
        return None
