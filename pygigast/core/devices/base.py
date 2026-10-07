"""機器の口（設計B §7.1・設計Q §2）。

コアから見た機器は「採用済みの掃引条件を渡すと、生の値の1回分が返る」もの。
"""
from __future__ import annotations

import abc
import json
import threading
from pathlib import Path
from typing import Callable, Optional

from ..types import AdoptedSweep, DeviceIdentity, RawSweep

# 1画面の進み具合を受け取る関数 (済んだ量, 全体の量)（設計J §4.13）
ProgressSink = Callable[[int, int], None]


def load_profile(path: Path) -> dict:
    """機種のプロファイル（JSON）を読む（設計Q §2.1）。"""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class Device(abc.ABC):
    """機器の口。実装は機器ごと（GigaSt・シミュレータ・将来の SDR）。

    progress_sink: measure の中で、1画面のうち機器が実際に済ませた量を知らせる先（設計J §4.13）。呼ぶ側（機器
    スレッド）が入れる。量は機器が返した実際の量で数え、時間からは出さない（機器が止まったら量も止まる）。
    全体の量は1画面に入る掃引の全部（イメージ除去の回・帯ごとの区間・NF の HOT/COLD）で、最後に済んだ量が
    全体の量に届いてから measure が戻る。
    """

    progress_sink: Optional[ProgressSink] = None

    def report_progress(self, done: int, total: int) -> None:
        """進み具合を知らせる（知らせる先が無ければ何もしない）。"""
        sink = self.progress_sink
        if sink is not None:
            sink(done, total)

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

    def on_reference_standard(self, standard: Optional[str]) -> None:
        """S11 の基準を取る間、いま測る標準器（"open"・"short"）を伝える。取り終えたら None（設計E §6.5）。

        実機は何もしない（利用者が繋ぎ替える）。シミュレータはその標準器を繋いだことにする。
        """
        return None
