"""PlutoSDR の切断の記録（設計B §7.7）に使う部品: 送信の LO に書いた値の見張り・接続した直後の送信の LO の読み・
再起動していたかの見分け・切れたときの詳細。

再起動の見分け（設計B §7.7）
- 戻ったとき、アプリが送信を静める（減衰を最小・LO を止める）・LO の下限を確かめる前に、送信の LO の周波数を読む
  （機器の口の open が _bind の直後に read_tx_lo で読む）
- 切れる前にアプリが書いた値と同じなら「再起動していない」、電源を入れた直後の値と同じなら「再起動した」、どちらでも
  ない・両方に当たる・読めない・アプリが一度も書いていないなら「不明」
- 電源を入れた直後の値は、Analog Devices の Linux の機器ツリー（analogdevicesinc/linux の zynq-pluto-sdr.dtsi）の
  adi,tx-synthesizer-frequency-hz = 2450000000。実機での読みはまだ確かめていない
- 「同じ」は SAME_TOL_HZ 以内。LO の読み戻しは書いた値から数 Hz ずれる（実機 2026-10-13: 48000000Hz を書くと
  47999998Hz）ので、ちょうど一致では見分けられない
"""
from __future__ import annotations

import sys
import time
from typing import Optional, Union

from ...device_events import iso

# 電源を入れた直後の送信の LO（Hz）。出典は上の説明
POWER_ON_TX_LO_HZ = 2_450_000_000
# 読んだ LO を「書いた値・電源投入時の値と同じ」とみなす幅（Hz）。PLL の読み戻しのずれ（数 Hz）より十分広くとった。
# アプリが書いた値が電源投入時の値からこの幅の中なら、両方に当たるので「不明」になる（取り違えない）
SAME_TOL_HZ = 100

Rebooted = Union[bool, str]  # True・False・"unknown"


class LoWatch:
    """送信の LO の通路を包み、書いた周波数を覚える（ほかの属性と読みはそのまま通す）。

    last_hz は最後に書けた値。unsure_hz は書こうとして失敗した値（切れる間際の書き込みは、機器に届いたかが
    分からないので、戻ったときの読みがこれと同じでも「再起動していない」とみなす）。"""

    def __init__(self, ch) -> None:
        self._ch = ch
        self.last_hz: Optional[int] = None
        self.unsure_hz: Optional[int] = None

    def set_attr(self, name: str, value) -> None:
        if name != "frequency":
            self._ch.set_attr(name, value)
            return
        hz = int(value)
        try:
            self._ch.set_attr(name, value)
        except Exception:
            self.unsure_hz = hz
            raise
        self.last_hz, self.unsure_hz = hz, None

    def written(self) -> list[int]:
        """書いたかもしれない値（覚えたもの全部）。"""
        return [v for v in (self.last_hz, self.unsure_hz) if v is not None]

    def __getattr__(self, name):
        return getattr(self._ch, name)


def read_tx_lo(ch) -> Optional[int]:
    """送信の LO の周波数（Hz）を読む。読めなければ None（接続は続ける: 記録のためだけに読む）。"""
    try:
        return int(round(float(str(ch.attr("frequency")).split()[0])))
    except Exception:  # noqa: BLE001 — 記録のための読みで接続を失敗にしない
        return None


def judge_reboot(read_hz: Optional[int], app_hz: list[int], default_hz: int = POWER_ON_TX_LO_HZ,
                 tol_hz: int = SAME_TOL_HZ) -> Rebooted:
    """戻ったときの送信の LO の読み read_hz から、再起動していたか（True・False・"unknown"）。"""
    if read_hz is None or not app_hz:
        return "unknown"
    same_app = any(abs(int(read_hz) - int(v)) <= tol_hz for v in app_hz)
    same_default = abs(int(read_hz) - int(default_hz)) <= tol_hz
    if same_app and not same_default:
        return False
    if same_default and not same_app:
        return True
    return "unknown"


def lost_detail(dev) -> dict:
    """切れたときに機器の口（PlutoSdrDevice）から残す詳細: 最後に受信（refill）・送信（push）が成り立った時刻、
    切れる前に書いた送信の LO、口がコンテキストを捨てたときの libiio の失敗（測定より前の待ちの間に捨てた道でも
    理由を残すため）。"""
    watch = getattr(dev, "_txlo", None)
    out = {"last_rx_refill_at": iso(getattr(dev, "last_rx_at", None)),
           "last_tx_push_at": iso(getattr(dev, "last_tx_at", None)),
           "tx_lo_written_hz": watch.written() if isinstance(watch, LoWatch) else []}
    err, at = getattr(dev, "drop_error", None), getattr(dev, "drop_at", None)
    if err is not None:
        out["drop_iio_function"] = f"iio_{err.func}"
        out["drop_errno"] = getattr(err, "errno", None)
        out["drop_strerror"] = getattr(err, "strerror", None)
        out["drop_at"] = iso(at)
    return out


def note_drop(dev) -> None:
    """口がコンテキストを捨てる（_drop）ときに呼ぶ。そのとき扱っている例外（_drop はどれも libiio の失敗の except の
    中から呼ばれる）が libiio の失敗なら覚える（最初の1回だけ）。"""
    exc = sys.exc_info()[1]
    if getattr(dev, "drop_error", None) is None and hasattr(exc, "func") and hasattr(exc, "code"):
        dev.drop_error, dev.drop_at = exc, time.time()
