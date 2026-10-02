"""機器の口（設計B §7.1・設計Q §2）。

コアから見た機器は「採用済みの掃引条件を渡すと、生の値の1回分が返る」もの。
"""
from __future__ import annotations

import abc
import json
import threading
from pathlib import Path
from typing import Optional

from ..types import AdoptedSweep, DeviceIdentity, RawSweep


def load_profile(path: Path) -> dict:
    """機種のプロファイル（JSON）を読む（設計Q §2.1）。"""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class Device(abc.ABC):
    """機器の口。実装は機器ごと（GigaSt・シミュレータ・将来の SDR）。"""

    @abc.abstractmethod
    def profile(self) -> dict:
        """機種のプロファイル（接続時に機器へ問い合わせて上書きした値を含む）。"""

    @abc.abstractmethod
    def identity(self) -> DeviceIdentity:
        """機器の身元。"""

    @abc.abstractmethod
    def measure(self, sweep: AdoptedSweep, cancel: Optional[threading.Event] = None) -> RawSweep:
        """1回測って生の値を返す。

        - 前置きの切り落とし・イメージ除去の区間ごとの回と合わせ方・NF の HOT/COLD の組はこの中で行う
        - 使えない掃引は SweepDiscarded、切れたら DeviceDisconnected、受け付けない条件は InvalidSweep
        - cancel が立ったら、いまの掃引の終わりを待ってから SweepDiscarded("cancelled") で戻る
          （掃引の途中で次の要求を送らない: 設計B §3・T6）
        """

    @abc.abstractmethod
    def close(self) -> None:
        """閉じる。"""
