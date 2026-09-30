"""マーカーの位置を聞く: 復調（設計Q §6.8）の試験。合成した信号で音の周波数と大きさを確かめる。"""
from __future__ import annotations

import time
import unittest

import numpy as np

from pygigast.core.listen import AUDIO_RATE, Demodulator, Mode

FS = 2_048_000  # RTL-SDR のサンプリング速度（設計Q §6.3）
OFFSET = 300_000  # 聞いている間のチューナーの中心からのずれ（設計Q §6.8）
TONE = 1_000.0
DUR = 0.5


def _t(n: int) -> np.ndarray:
    return np.arange(n) / FS


def _carrier(n: int, off: float = OFFSET) -> np.ndarray:
    return np.exp(2j * np.pi * off * _t(n))


def _peak(audio: np.ndarray) -> tuple[float, float]:
    """音の後半（フィルタの立ち上がりを除く）の FFT で、いちばん大きい周波数と振幅を返す。"""
    a = audio[audio.size // 2:]
    a = a - a.mean()
    w = np.hanning(a.size)
    spec = np.abs(np.fft.rfft(a * w)) * 2 / w.sum()
    k = int(np.argmax(spec[1:])) + 1
    return k * AUDIO_RATE / a.size, float(spec[k])


def _run(mode: Mode, iq: np.ndarray, offset: float = OFFSET, blocks: int = 1) -> np.ndarray:
    d = Demodulator(mode, FS, offset)
    parts = np.array_split(iq.astype(np.complex64), blocks)
    return np.concatenate([d.process(p) for p in parts])


class ToneTests(unittest.TestCase):
    n = int(FS * DUR)

    def assert_tone(self, audio: np.ndarray, amp: float, tol_db: float = 1.5) -> None:
        self.assertEqual(audio.dtype, np.float32)
        # 出力の標本数は 48kHz × 長さ（フィルタの遅れ分だけ少ない）
        self.assertAlmostEqual(audio.size, AUDIO_RATE * DUR, delta=AUDIO_RATE * 0.01)
        f, a = _peak(audio)
        self.assertAlmostEqual(f, TONE, delta=AUDIO_RATE / (audio.size / 2) * 1.5)
        self.assertLess(abs(20 * np.log10(a / amp)), tol_db, f"amp {a} vs {amp}")

    def test_am(self):
        A, m = 0.5, 0.5
        env = A * (1 + m * np.cos(2 * np.pi * TONE * _t(self.n)))
        self.assert_tone(_run(Mode.AM, env * _carrier(self.n)), A * m)

    def test_fm_narrow(self):
        dev = 1_250.0  # 最大の偏移 2.5kHz の半分 → 音の振幅 0.5
        ph = 2 * np.pi * OFFSET * _t(self.n) + dev / TONE * np.sin(2 * np.pi * TONE * _t(self.n))
        self.assert_tone(_run(Mode.FM_NARROW, 0.3 * np.exp(1j * ph)), 0.5)

    def test_fm_broadcast(self):
        dev = 37_500.0  # 最大の偏移 75kHz の半分
        ph = 2 * np.pi * OFFSET * _t(self.n) + dev / TONE * np.sin(2 * np.pi * TONE * _t(self.n))
        # ディエンファシス 50µs の 1kHz での減衰: 1/√(1+(2π·1k·50µ)²)
        g = 1 / np.sqrt(1 + (2 * np.pi * TONE * 50e-6) ** 2)
        self.assert_tone(_run(Mode.FM_BROADCAST, 0.3 * np.exp(1j * ph)), 0.5 * g)

    def test_usb_and_lsb(self):
        A = 0.3
        usb_sig = A * _carrier(self.n, OFFSET + TONE)
        lsb_sig = A * _carrier(self.n, OFFSET - TONE)
        self.assert_tone(_run(Mode.USB, usb_sig), A)
        self.assert_tone(_run(Mode.LSB, lsb_sig), A)
        # 反対側の側波帯は落ちる（40dB 以上）
        _, a_wrong = _peak(_run(Mode.LSB, usb_sig))
        self.assertLess(a_wrong, A / 100)

    def test_level_order(self):
        """強い信号ほど大きな音（AM・SSB は自動の利得を持たない）。"""
        weak = _peak(_run(Mode.USB, 0.01 * _carrier(self.n, OFFSET + TONE)))[1]
        strong = _peak(_run(Mode.USB, 0.1 * _carrier(self.n, OFFSET + TONE)))[1]
        self.assertAlmostEqual(20 * np.log10(strong / weak), 20.0, delta=0.5)

    def test_other_signal_outside_channel_is_rejected(self):
        """チャンネルの外（100kHz 離れた）の強い搬送波は音に出ない。"""
        A = 0.5
        env = A * (1 + 0.5 * np.cos(2 * np.pi * TONE * _t(self.n)))
        iq = env * _carrier(self.n) + 0.5 * _carrier(self.n, OFFSET + 100_000)
        self.assert_tone(_run(Mode.AM, iq), A * 0.5)


class StreamingTests(unittest.TestCase):
    def test_blocks_give_same_audio(self):
        """塊に分けても一度に処理しても同じ音（境目で途切れない・鳴らない）。"""
        n = int(FS * 0.2)
        t = _t(n)
        for mode in Mode:
            with self.subTest(mode=mode.value):
                ph = 2 * np.pi * OFFSET * t + 2.0 * np.sin(2 * np.pi * TONE * t)
                iq = (0.3 * (1 + 0.3 * np.cos(2 * np.pi * 700 * t))) * np.exp(1j * ph)
                one = _run(mode, iq, blocks=1)
                # 不揃いな大きさの塊（小さい塊も混ぜる）
                d = Demodulator(mode, FS, OFFSET)
                cuts = [0, 1, 7, 5000, 5001, 70_000, 123_457, n]
                many = np.concatenate([d.process(iq[a:b].astype(np.complex64)) for a, b in zip(cuts, cuts[1:])])
                m = min(one.size, many.size)
                self.assertLessEqual(abs(one.size - many.size), 1)
                np.testing.assert_allclose(many[:m], one[:m], atol=2e-4)

    def test_set_offset_keeps_running(self):
        n = int(FS * 0.1)
        d = Demodulator(Mode.AM, FS, OFFSET)
        a = d.process(_carrier(n).astype(np.complex64))
        d.set_offset(OFFSET + 10_000)
        b = d.process(_carrier(n, OFFSET + 10_000).astype(np.complex64))
        self.assertGreater(a.size, 0)
        self.assertGreater(b.size, 0)
        self.assertTrue(np.all(np.isfinite(b)))

    def test_realtime_speed(self):
        """1秒分の 2.048MS/s を 1秒より十分短く処理できる（聞きながら表示も更新するため）。"""
        n = FS
        iq = (0.3 * _carrier(n)).astype(np.complex64)
        for mode in Mode:
            d = Demodulator(mode, FS, OFFSET)
            t0 = time.perf_counter()
            for p in np.array_split(iq, 16):
                d.process(p)
            dt = time.perf_counter() - t0
            with self.subTest(mode=mode.value):
                self.assertLess(dt, 0.7, f"{mode.value}: {dt:.2f}s")


if __name__ == "__main__":
    unittest.main()
