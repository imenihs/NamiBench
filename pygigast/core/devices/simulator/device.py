"""偽の GigaSt（設計B §4・§7.4）。

実機と同じ口（measure）を持ち、実機と同じ32バイトの要求を protocol で組み立てて検証する。
送受信は実機と同じ Transport を、偽のポート（SimulatedPort）の上で動かす——3つの上限時間・
バイト数の検査・受信バッファを捨てる手順・切断の扱いが、実機の経路と同じコードで試される。

- 再生: ref/capture の実機の応答を、要求（32バイト）が一致するものから返す。一致が無ければ合成に落ちる
- 合成: ノイズフロア（AD 90〜130）＋指定した信号＋0Hz の局発の漏れ＋プロファイルの内部スプリアス。
  掃引ごとに位置の変わる山（T16）を足す設定を持つ（既定は出さない。イメージキャンセルの4回の試験用）
  時間は設計B §7.2 の見積もりの式で流す（speed で縮められる）
- 異常系: 欠けた応答・末尾の無い応答・応答なし・余計な1バイト・切断を、次の要求に起こせる
"""
from __future__ import annotations

import copy
import itertools
import re
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import serial

from ...types import DeviceDisconnected, DeviceIdentity
from ..base import Device
from ..gigast import protocol
from ..gigast.driver import SweepRunner
from ..gigast.protocol import DeviceSweep, Limits
from ..gigast.traffic_log import TrafficLog, read_log
from ..gigast.transport import READ_TIMEOUT_S, Exchange, Transport

AD_PER_DB = 1986 / 80  # 1986 count = 80dB（プロファイル raw.ad_counts_per_80db と同じ）
AD_MAX = 4095
FAULTS = ("truncate", "no_trailer", "no_response", "stray_byte", "disconnect")
# 速くしても上限時間を縮めすぎない（Windows の sleep の粒度で誤って打ち切らないため）
MIN_LIMIT_S = 0.25


def dbm_to_ad(dbm: float) -> float:
    """補正なしの換算の逆（P = AD×80/1986 − 80）。合成の値を作るためだけに使う。"""
    return (dbm + 80.0) * AD_PER_DB


@dataclass
class SynthConfig:
    """合成の条件。レベルは補正なしの dBm（AD と 1986 count = 80dB で結ぶ）。"""

    signals: list[tuple[int, float]] = field(default_factory=list)  # (周波数 Hz, dBm) の入力信号
    noise_ad: tuple[int, int] = (90, 130)
    lo_leak_dbm: float = 1.9  # 0Hz の局発の漏れ（T4・§6.8 の実測に近い値）
    spur_dbm: float = -27.0  # 内部スプリアスのレベル
    # b3 を立てると動くスプリアス（T2: 956MHz は b3 で 946MHz へ動き、1480MHz は動かない）
    moving_spurs_hz: tuple[int, ...] = (956_000_000,)
    b3_spur_shift_hz: int = -10_000_000
    sg_dbm: float = -15.0  # SG・TG の出力（直結で受けたときのレベル）
    b3_level_db: float = -1.0  # b3=1 は b3=0 より約1dB 低い（T2）
    nf_hot_db: float = 3.0  # NF の HOT でノイズフロアが上がる量
    # 掃引ごとに位置の変わる山（T16: 雑音の上に幅 10〜30kHz・高さ約 10dB の平らな山。0〜1000MHz で出る）。
    # 1回の掃引に出す数（0 は出さない）・幅・雑音の上の高さ・出る帯。値は T16 の観察に近い仮の値
    bursts_per_sweep: int = 0
    burst_width_hz: int = 20_000
    burst_db: float = 10.0
    burst_band_hz: tuple[int, int] = (0, 1_000_000_000)


class Synth:
    """要求から AD 値の並びを作る。"""

    def __init__(self, profile: dict, config: SynthConfig, seed: Optional[int] = None) -> None:
        self.profile = profile
        self.config = config
        self.rng = np.random.default_rng(seed)

    def response(self, ds: DeviceSweep) -> np.ndarray:
        c = self.config
        n = ds.points
        f = ds.start_hz + ds.step_hz * np.arange(n, dtype=np.int64)
        lo, hi = c.noise_ad
        ad = self.rng.integers(lo, hi + 1, size=n).astype(np.float64)
        if ds.mode_bits & protocol.MODE_B2:
            ad += c.nf_hot_db * AD_PER_DB
        b3 = bool(ds.mode_bits & protocol.MODE_B3)
        # 1点は [f, f + Sample × 増加周波数) を max で集約する（T3 の推定）
        width = ds.sample * ds.sample_inc_units * protocol.UNIT_HZ
        tones: list[tuple[int, float]] = [(0, c.lo_leak_dbm)]
        tones += [(fs, lv + (c.b3_level_db if b3 else 0.0)) for fs, lv in c.signals]
        for fs in self.profile["internal_spurs_hz"]["fixed"]:
            if b3 and fs in c.moving_spurs_hz:
                fs += c.b3_spur_shift_hz
            tones.append((fs, c.spur_dbm))
        if ds.mode_bits & protocol.MODE_SG:
            tones.append((ds.sg_hz, c.sg_dbm + (c.b3_level_db if b3 else 0.0)))
            for off in self.profile["internal_spurs_hz"]["sg_offset"]:
                tones.append((ds.sg_hz + off, c.spur_dbm))
        for fs, lv in tones:
            df = np.where(f > fs, f - fs, np.maximum(0, fs - (f + width)))
            shape_db = -3.0 * (df / (ds.rbw_hz / 2)) ** 2
            ad = np.maximum(ad, dbm_to_ad(lv + np.maximum(shape_db, -120.0)))
        if c.bursts_per_sweep > 0:
            ad = self._add_bursts(ad, f)
        if ds.mode_bits & protocol.MODE_TG:
            ad = np.maximum(ad, dbm_to_ad(c.sg_dbm + (c.b3_level_db if b3 else 0.0))
                            + self.rng.normal(0, 3, size=n))
        return np.clip(np.rint(ad), 0, AD_MAX).astype("<u2")


    def _add_bursts(self, ad: np.ndarray, f: np.ndarray) -> np.ndarray:
        """雑音の床に、掃引ごとに違う位置の平らな山を乗せる（T16。信号の上には乗らない: max で重ねる）。"""
        c = self.config
        lo_band, hi_band = c.burst_band_hz
        lo = max(int(f[0]), lo_band)
        hi = min(int(f[-1]), hi_band)
        if hi <= lo:
            return ad
        top = c.noise_ad[1] + c.burst_db * AD_PER_DB
        for center in self.rng.integers(lo, hi + 1, size=c.bursts_per_sweep):
            inside = np.abs(f - int(center)) <= c.burst_width_hz // 2
            ad = np.where(inside, np.maximum(ad, top), ad)
        return ad


class ReplayIndex:
    """要求（32バイト）→ 実機の応答の並び。同じ要求が何回もあれば順に返す。"""

    def __init__(self) -> None:
        self._by_request: dict[bytes, list[bytes]] = {}
        self._cycles: dict[bytes, itertools.cycle] = {}

    def add(self, request: bytes, response: bytes) -> None:
        self._by_request.setdefault(bytes(request), []).append(bytes(response))
        self._cycles.pop(bytes(request), None)

    def __len__(self) -> int:
        return sum(len(v) for v in self._by_request.values())

    def lookup(self, request: bytes) -> Optional[bytes]:
        r = bytes(request)
        if r not in self._by_request:
            return None
        if r not in self._cycles:
            self._cycles[r] = itertools.cycle(self._by_request[r])
        return next(self._cycles[r])

    @classmethod
    def from_paths(cls, paths: Iterable[Path | str]) -> "ReplayIndex":
        """送受信の記録（.jsonl）と、実機の受信データのフォルダ（README.md の条件表と .bin）を読む。"""
        idx = cls()
        for p in map(Path, paths):
            if p.is_file() and p.suffix == ".jsonl":
                idx._add_log(p)
            elif p.is_dir():
                for j in sorted(p.rglob("*.jsonl")):
                    idx._add_log(j)
                for readme in sorted(p.rglob("README.md")):
                    idx._add_readme(readme)
        return idx

    def _add_log(self, path: Path) -> None:
        try:
            for req, raw, _row in read_log(path):
                self.add(req, raw)
        except (OSError, ValueError, KeyError):
            pass  # 形の違う JSONL（別の道具の記録）は読まない

    def _add_readme(self, readme: Path) -> None:
        """条件表の列（ファイル・Start(MHz)・RBW(kHz)・MODE・SG(MHz)・Step(100Hz)・Plot・Sample）から要求を作る。

        Step・Plot・Sample の列が無い表は、要求を一意に作れないので読まない。
        """
        header: Optional[list[str]] = None
        for line in readme.read_text(encoding="utf-8").splitlines():
            if not line.startswith("|"):
                header = None
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if cells and cells[0] == "ファイル":
                header = cells
                continue
            if header is None or re.fullmatch(r"[-: ]+", "".join(cells)):
                continue
            row = dict(zip(header, cells))
            need = ("Start(MHz)", "RBW(kHz)", "MODE", "SG(MHz)", "Step(100Hz)", "Plot", "Sample")
            if not all(k in row for k in need):
                continue
            binf = readme.parent / row["ファイル"]
            if not binf.is_file():
                continue
            try:
                rbw_hz = int(row["RBW(kHz)"]) * 1000
                ds = DeviceSweep(
                    start_hz=int(Decimal(row["Start(MHz)"]) * 1_000_000),
                    step_hz=int(row["Step(100Hz)"]) * protocol.UNIT_HZ,
                    points=int(row["Plot"]),
                    sample=int(row["Sample"]),
                    sample_inc_units=protocol.sample_inc_units(rbw_hz),
                    rbw_hz=rbw_hz,
                    mode_bits=int(row["MODE"], 16),
                    sg_hz=int(Decimal(row["SG(MHz)"]) * 1_000_000),
                )
                if ds.points < 1 or ds.points > 0xFFFF:
                    continue
                self.add(protocol.pack(ds), binf.read_bytes())
            except (ValueError, ArithmeticError, KeyError, OSError, struct.error):
                continue  # 読めない行（表の注記など）は飛ばす


class SimulatedPort:
    """pyserial の Serial の代わり（Transport が使う分だけ）。応答は時刻の表に従って届く。"""

    def __init__(self, profile: dict, synth: Synth, replay: Optional[ReplayIndex], speed: float) -> None:
        self.profile = profile
        self.synth = synth
        self.replay = replay
        self.speed = speed
        self.timeout = max(READ_TIMEOUT_S * speed, 0.001)
        self._lock = threading.Lock()
        self._pending: deque[tuple[float, int]] = deque()  # (届く時刻, バイト)
        self._busy_until = 0.0  # 機器が掃引している間（途中の要求は無視して、後で 00 を1バイト返す: T6）
        self.faults: deque[str] = deque()
        self.unplugged = False
        self.last_source: Optional[str] = None  # "replay" か "synth"（試験で確かめる）
        self.requests: list[bytes] = []

    # ---- 異常系 ----
    def inject(self, fault: str) -> None:
        if fault not in FAULTS:
            raise ValueError(f"知らない異常: {fault}")
        self.faults.append(fault)

    def _check(self) -> None:
        if self.unplugged:
            raise serial.SerialException("ClearCommError failed (simulated unplug)")

    # ---- PortLike ----
    @property
    def in_waiting(self) -> int:
        now = time.monotonic()
        with self._lock:
            n = 0
            for t, _ in self._pending:
                if t > now:
                    break
                n += 1
            return n

    def reset_input_buffer(self) -> None:
        self._check()
        now = time.monotonic()
        with self._lock:
            while self._pending and self._pending[0][0] <= now:
                self._pending.popleft()

    def write(self, data: bytes) -> int:
        self._check()
        now = time.monotonic()
        self.requests.append(bytes(data))
        if now < self._busy_until:
            # 掃引の途中の要求は無視され、いまの掃引の後に 00 が1バイト来る（T6）
            with self._lock:
                self._pending.append((self._busy_until + 0.19 * self.speed, 0))
            return len(data)
        fault = self.faults.popleft() if self.faults else None
        if fault == "disconnect":
            self.unplugged = True
            self._check()
        ds = protocol.unpack(bytes(data))
        resp = self.replay.lookup(data) if self.replay is not None else None
        if resp is not None:
            self.last_source = "replay"
        else:
            self.last_source = "synth"
            resp = self.synth.response(ds).tobytes() + bytes.fromhex("48431ca5")  # 末尾は意味の無いダミー
        first_s, per_point_s = protocol.timing(self.profile, ds.step_hz, ds.rbw_hz, protocol.timing_mode(ds))
        first = first_s * self.speed
        per_byte = per_point_s * self.speed / 2
        if fault == "truncate":
            # 途中が欠けて末尾だけ届く形（§6.5 の 305 バイトの例）
            resp = resp[: len(resp) // 3] + resp[-4:]
        elif fault == "no_trailer":
            resp = resp[:-4]
        elif fault == "no_response":
            resp = b""
        times = now + first + per_byte * np.arange(len(resp))
        end = float(times[-1]) if len(resp) else now + first
        with self._lock:
            self._pending.extend(zip(times.tolist(), resp))
            if fault == "stray_byte":
                self._pending.append((end + 0.19 * self.speed, 0))
        self._busy_until = end
        return len(data)

    def read(self, size: int = 1) -> bytes:
        deadline = time.monotonic() + self.timeout
        while True:
            self._check()
            now = time.monotonic()
            with self._lock:
                n = 0
                while n < len(self._pending) and n < size and self._pending[n][0] <= now:
                    n += 1
                if n >= size or now >= deadline:
                    out = bytes(self._pending.popleft()[1] for _ in range(n))
                    return out
            time.sleep(min(0.001, max(0.0, deadline - now)))

    def close(self) -> None:
        pass


class SimulatorDevice(SweepRunner, Device):
    """偽の GigaSt。GigaSt と同じプロファイル・同じ要求・同じ送受信のコードで動く。"""

    def __init__(
        self,
        profile: Optional[dict] = None,
        config: Optional[SynthConfig] = None,
        speed: float = 1.0,
        replay: Optional[ReplayIndex] = None,
        seed: Optional[int] = None,
        traffic_log: Optional[TrafficLog] = None,
        serial_number: str = "SIM00001",
    ) -> None:
        self._profile = copy.deepcopy(profile if profile is not None else protocol.default_profile())
        self.speed = speed
        self.config = config or SynthConfig()
        self.port = SimulatedPort(self._profile, Synth(self._profile, self.config, seed), replay, speed)
        self._transport: Optional[Transport] = Transport(self.port, traffic_log)
        self._traffic_log = traffic_log
        self._identity = DeviceIdentity(model=self._profile["model"], serial=serial_number, port="simulator")

    # ---- 異常系・抜き挿し ----
    def inject(self, fault: str) -> None:
        """次の要求に異常を起こす（truncate・no_trailer・no_response・stray_byte・disconnect）。"""
        self.port.inject(fault)

    def unplug(self) -> None:
        self.port.unplugged = True

    def plug(self) -> None:
        """挿し直す（実機と同じく、開き直した最初の掃引から使える: T8）。"""
        self.port.unplugged = False
        self._transport = Transport(self.port, self._traffic_log)

    # ---- 機器の口 ----
    def profile(self) -> dict:
        return self._profile

    def identity(self) -> DeviceIdentity:
        return self._identity

    def _limits(self, ds: DeviceSweep) -> Limits:
        lim = protocol.limits(self._profile, ds.step_hz, ds.rbw_hz, ds.points, mode=protocol.timing_mode(ds))
        s = self.speed
        return Limits(
            first_byte_s=max(lim.first_byte_s * s, MIN_LIMIT_S),
            silence_s=max(lim.silence_s * s, MIN_LIMIT_S),
            total_s=max(lim.total_s * s, MIN_LIMIT_S * 2),
            estimated_s=lim.estimated_s * s,
        )

    def _exchange(self, request: bytes, expected: int, lim: Limits, note: Optional[str]) -> Exchange:
        if self._transport is None:
            raise DeviceDisconnected("切断中")
        try:
            return self._transport.exchange(request, expected, lim, note)
        except DeviceDisconnected:
            self._transport = None
            raise

    def close(self) -> None:
        self._transport = None
