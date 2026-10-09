"""libiio の偽物（PlutoSDR のシミュレータ。設計Q §7: 実機が無くても全部の試験を回す）。

iio.py の IioLib と同じ口（scan・open → 文脈・機器・通路・バッファ）を持ち、受信のバッファには
「入力の信号（周波数と dBm）」と、このアプリが出した送信（SG のトーン・TG の多トーン）を混ぜた IQ を作る。
レベルの決まりは単純にしてある: 振り切れに対する dBFS = 入力の dBm ＋ 受信の利得。雑音は全帯域で −95 ＋ 利得 dBFS。
送信は「送信の口と受信の口を減衰 loss_db で直結した」とみなす（SG・TG の試験用。dut があればその通過特性を掛ける）。
数値は試験と画面の確認のためのもので、実機の値ではない。
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

import numpy as np

from .iio import IioError

FAKE_SERIAL = "FAKEPLUTO0001"
FAKE_URI = "ip:192.168.2.1"
FAKE_USB_URI = "usb:9.9.9"

_GAIN_BANDS = ((1_300_000_000, -1, 73), (4_000_000_000, -3, 71), (6_000_000_001, -10, 62))
_EINVAL = -22
_EIO = -5


def _band(lo_hz: float) -> tuple[int, int]:
    for top, mn, mx in _GAIN_BANDS:
        if lo_hz < top:
            return mn, mx
    return _GAIN_BANDS[-1][1], _GAIN_BANDS[-1][2]


class FakePluto:
    """機器1台の中身（属性と送受信）。試験はこれを触って状態を作り、書き込みの記録を見る。"""

    def __init__(self, signals=None, serial: str = FAKE_SERIAL, seed: int = 1, loss_db: float = 30.0,
                 dut: Optional[Callable[[np.ndarray], np.ndarray]] = None) -> None:
        self.serial = serial
        self.signals: list[tuple[int, float]] = list(signals or [])
        self.loss_db = float(loss_db)
        self.dut = dut  # 周波数の配列（Hz）→ 通過の振幅比（送信を受信へ通すとき掛ける）
        self.rng = np.random.default_rng(seed)
        self.unplugged = False
        self.log: list[tuple[str, str, str]] = []  # (通路, 属性, 値) の書き込み
        self.lock = threading.Lock()
        self.attrs: dict[tuple[str, str, bool, str], str] = {}
        p = ("ad9361-phy",)
        self._set_raw(p[0], "voltage0", False, "sampling_frequency", "4000000")
        self._set_raw(p[0], "voltage0", False, "rf_bandwidth", "2333332")
        self._set_raw(p[0], "voltage0", False, "gain_control_mode", "manual")
        self._set_raw(p[0], "voltage0", False, "hardwaregain", "54.000000 dB")
        self._set_raw(p[0], "voltage0", True, "hardwaregain", "-10.000000 dB")
        self._set_raw(p[0], "voltage0", True, "hardwaregain_available", "[-89.750000 0.250000 0.000000]")
        self._set_raw(p[0], "altvoltage0", True, "frequency", "84699998")
        self._set_raw(p[0], "altvoltage0", True, "frequency_available", "[70000000 1 6000000000]")
        self._set_raw(p[0], "altvoltage1", True, "frequency", "2450000000")
        self._set_raw(p[0], "altvoltage1", True, "frequency_available", "[46875001 1 6000000000]")
        self._set_raw(p[0], "altvoltage1", True, "powerdown", "0")
        for i, ph in enumerate(("90000", "90000", "0", "0")):
            for k, v in (("raw", "1"), ("frequency", "3999511"), ("scale", "0.000000"), ("phase", ph)):
                self._set_raw("cf-ad9361-dds-core-lpc", f"altvoltage{i}", True, k, v)
        self.tx_buffer: Optional[np.ndarray] = None  # 送っている循環バッファ（複素数・振り切れ 1.0）

    # ---- 属性 ----
    def _set_raw(self, dev, ch, out, name, value) -> None:
        self.attrs[(dev, ch, out, name)] = str(value)

    def get(self, dev, ch, out, name) -> str:
        if (dev, ch, out, name) == ("ad9361-phy", "voltage0", False, "hardwaregain_available"):
            mn, mx = _band(self.rx_lo)
            return f"[{mn} 1 {mx}]"
        if (dev, ch, out, name) not in self.attrs:
            raise IioError("channel_attr_read", -2, f"{dev}/{ch}/{name}")
        return self.attrs[(dev, ch, out, name)]

    def put(self, dev, ch, out, name, value) -> None:
        if self.unplugged:
            raise IioError("channel_attr_write", _EIO, "unplugged")
        v = str(value)
        if dev == "ad9361-phy" and ch == "voltage0" and not out and name == "hardwaregain":
            mn, mx = _band(self.rx_lo)
            g = float(v)
            if not mn <= g <= mx:
                raise IioError("channel_attr_write", _EINVAL, "Invalid argument")
            v = f"{g:.6f} dB"
        self.log.append((f"{dev}/{ch}{'_out' if out else ''}", name, str(value)))
        self.attrs[(dev, ch, out, name)] = v

    def _num(self, dev, ch, out, name) -> float:
        return float(self.attrs[(dev, ch, out, name)].split()[0])

    @property
    def rx_lo(self) -> float:
        return self._num("ad9361-phy", "altvoltage0", True, "frequency")

    @property
    def fs(self) -> float:
        return self._num("ad9361-phy", "voltage0", False, "sampling_frequency")

    def tx_writes(self) -> list[tuple[str, str, str]]:
        """送信に関わる書き込み（送信の LO・送信の減衰・DDS・送信のバッファ）。"""
        return [w for w in self.log if w[0].startswith("cf-ad9361-dds-core-lpc") or w[0] == "ad9361-phy/altvoltage1_out"
                or w[0] == "ad9361-phy/voltage0_out"]

    # ---- 受信の標本 ----
    def rx_gain(self) -> float:
        mode = self.attrs[("ad9361-phy", "voltage0", False, "gain_control_mode")]
        if mode != "manual":
            return 40.0
        return self._num("ad9361-phy", "voltage0", False, "hardwaregain")

    def samples(self, n: int) -> np.ndarray:
        """受信の n 標本（int16 の I・Q の交互）。"""
        if self.unplugged:
            raise IioError("buffer_refill", _EIO, "unplugged")
        fs = self.fs
        lo = self.rx_lo
        g = self.rx_gain()
        t = np.arange(n) / fs
        z = np.zeros(n, dtype=np.complex128)
        for f, dbm in self.signals:
            off = f - lo
            if abs(off) < fs / 2:
                a = 10 ** ((dbm + g) / 20.0)
                z += a * np.exp(2j * np.pi * off * t + 1j * self.rng.uniform(0, 2 * np.pi))
        z += self._tx_into_rx(n, t, lo, g)
        nz = 10 ** ((-95.0 + g) / 20.0) / np.sqrt(2)
        z += nz * (self.rng.standard_normal(n) + 1j * self.rng.standard_normal(n))
        x = np.clip(np.round(z * 2048.0), -2048, 2047)
        out = np.empty(2 * n, dtype=np.int16)
        out[0::2] = x.real.astype(np.int16)
        out[1::2] = x.imag.astype(np.int16)
        return out

    def _tx_level_scale(self) -> float:
        atten = self._num("ad9361-phy", "voltage0", True, "hardwaregain")
        return 10 ** ((atten - self.loss_db) / 20.0)

    def _tx_into_rx(self, n: int, t: np.ndarray, rx_lo: float, gain: float) -> np.ndarray:
        if self.attrs[("ad9361-phy", "altvoltage1", True, "powerdown")] != "0":
            return np.zeros(n, dtype=np.complex128)
        tx_lo = self._num("ad9361-phy", "altvoltage1", True, "frequency")
        k = self._tx_level_scale() * 10 ** (gain / 20.0)
        d = "cf-ad9361-dds-core-lpc"
        if self.tx_buffer is not None:
            x = self.tx_buffer
            m = x.size
            base = np.resize(x, n)
            if self.dut is not None:
                spec = np.fft.fft(x)
                freqs = tx_lo + np.fft.fftfreq(m, 1.0 / self.fs)
                base = np.resize(np.fft.ifft(spec * self.dut(freqs)), n)
            return k * base * np.exp(2j * np.pi * (tx_lo - rx_lo) * t)
        s = float(self.attrs[(d, "altvoltage0", True, "scale")])
        if s <= 0:
            return np.zeros(n, dtype=np.complex128)
        f = float(self.attrs[(d, "altvoltage0", True, "frequency")])
        off = tx_lo + f - rx_lo
        if abs(off) >= self.fs / 2:
            return np.zeros(n, dtype=np.complex128)
        a = k * s
        if self.dut is not None:
            a = a * float(np.abs(self.dut(np.array([tx_lo + f]))[0]))
        return a * np.exp(2j * np.pi * off * t)


class _Ctx:
    def __init__(self, dev: FakePluto, uri: str) -> None:
        self.dev = dev
        self.uri = uri
        self.closed = False

    @property
    def is_open(self) -> bool:
        return not self.closed

    def close(self) -> None:
        self.closed = True

    def set_timeout(self, ms: int) -> None:
        pass

    def attr(self, name: str) -> Optional[str]:
        return {"hw_serial": self.dev.serial, "hw_model": "Analog Devices PlutoSDR Rev.B (Z7010-AD9364)",
                "fw_version": "v0.39-fake", "uri": self.uri}.get(name)

    def device(self, name: str) -> "_Dev":
        if name not in ("ad9361-phy", "cf-ad9361-lpc", "cf-ad9361-dds-core-lpc"):
            raise IioError("context_find_device", 0, name)
        return _Dev(self, name)


class _Dev:
    def __init__(self, ctx: _Ctx, name: str) -> None:
        self.ctx = ctx
        self.name = name

    def channel(self, name: str, output: bool) -> "_Ch":
        return _Ch(self, name, bool(output))

    def attr(self, name: str) -> str:
        return self.ctx.dev.get(self.name, "", False, name)

    def set_attr(self, name: str, value) -> None:
        self.ctx.dev.put(self.name, "", False, name, value)

    def create_buffer(self, samples: int, cyclic: bool = False) -> "_Buf":
        if self.ctx.dev.unplugged:
            raise IioError("device_create_buffer", 0, "unplugged")
        return _Buf(self, int(samples), bool(cyclic))


class _Ch:
    def __init__(self, dev: _Dev, name: str, out: bool) -> None:
        self.dev = dev
        self.name = name
        self.out = out

    def attr(self, name: str) -> str:
        return self.dev.ctx.dev.get(self.dev.name, self.name, self.out, name)

    def set_attr(self, name: str, value) -> None:
        self.dev.ctx.dev.put(self.dev.name, self.name, self.out, name, value)

    def enable(self) -> None:
        pass

    def disable(self) -> None:
        pass


class _Buf:
    def __init__(self, dev: _Dev, samples: int, cyclic: bool) -> None:
        self.dev = dev
        self.samples = samples
        self.cyclic = cyclic
        self._alive = True

    def refill(self) -> np.ndarray:
        return self.dev.ctx.dev.samples(self.samples)

    def push(self, iq: np.ndarray) -> None:
        a = np.asarray(iq, dtype=np.int16).astype(np.float64)
        pl = self.dev.ctx.dev
        pl.tx_buffer = (a[0::2] + 1j * a[1::2]) / 32767.0
        pl.log.append(("cf-ad9361-dds-core-lpc/buffer_out", "push", str(self.samples)))

    def destroy(self) -> None:
        if self._alive and self.dev.name == "cf-ad9361-dds-core-lpc":
            self.dev.ctx.dev.tx_buffer = None
            self.dev.ctx.dev.log.append(("cf-ad9361-dds-core-lpc/buffer_out", "destroy", ""))
        self._alive = False


class FakeIioLib:
    """IioLib の偽物。devices は URI → FakePluto（既定は ip:192.168.2.1 と USB の両方に同じ1台）。"""

    def __init__(self, pluto: Optional[FakePluto] = None, uris=(FAKE_URI, FAKE_USB_URI)) -> None:
        self.pluto = pluto or FakePluto(signals=[(84_700_000, -40.0), (90_000_000, -55.0)])
        self.uris = tuple(uris)
        self.opened: list[str] = []

    def version(self) -> tuple[int, int, str]:
        return 0, 26, "fake"

    def strerror(self, code: int) -> str:
        return f"error {code}"

    def scan(self, backend: str = "usb") -> list[tuple[str, str]]:
        if self.pluto.unplugged:
            return []
        return [(u, f"0456:b673 (Analog Devices Inc. PlutoSDR (ADALM-PLUTO)), serial={self.pluto.serial}")
                for u in self.uris if u.startswith("usb:") and backend == "usb"]

    def open(self, uri: str, timeout_ms: int = 3000) -> _Ctx:
        if self.pluto.unplugged or uri not in self.uris:
            raise IioError("create_context_from_uri", 0, uri)
        self.opened.append(uri)
        return _Ctx(self.pluto, uri)

    def reachable(self, uri: str) -> bool:
        return not self.pluto.unplugged and uri in self.uris
