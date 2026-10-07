"""掃引の進み具合（設計J §4.13）。

- 100% は画面が1回更新されるまで。1画面に入る掃引（イメージ除去の回・帯ごとの区間・NF の HOT/COLD）を全部含む
- 割合は機器が実際に済ませた量から出す（GigaSt・シミュレータは届いた点の数、RTL-SDR は周波数を動かした段の数:
  Device.progress_sink）。時間からは出さない（機器が止まったら棒も止まり、止まったことが見える）
- 出すのは1画面の見積もりが SHOW_ABOVE_S を超えるときだけ
- 画面へはイベント sweep_progress {"show", "done", "total"} で送る（状態を全部送り直さない）。前の知らせから
  MIN_INTERVAL_S は空ける（1秒に10回まで）。間に来た値は最後の1つだけを残し、間隔が明けたら送る
- ただし画面が更新されたときの 100% は間隔によらずすぐ送り、ほかの値で置き換えない。次の値（次の掃引の 0%・隠す）
  はその MIN_INTERVAL_S 後まで待たせる（100% が少なくとも1間隔は見える）。間引きに入れると、100% は次の掃引の 0%
  か隠すにすぐ置き換わって出なかった（2026-10-07 反証: 連続で 17 画面のうち 6 画面しか 100% にならなかった）。
  このため画面の更新の前後だけは、間隔が MIN_INTERVAL_S より短い知らせが1つ増える

機器スレッドの側（ProgressReporter）は最新の量を置くだけで、制御スレッドの側（SweepProgressMixin）が
MIN_INTERVAL_S ごとに読みに行く。機器スレッドから量ごとに送ると、点が1つずつ届く掃引では待ち行列が溢れ、
間引いて送ると、機器が止まる直前の量が届かない（止まった後は量が来ないので、間引いた最後の量を送る折が無い）。
"""
from __future__ import annotations

import math
import time
from typing import Callable, Optional

from .types import AdoptedSweep

# 1画面の見積もりがこれを超えるときだけ出す（設計J §4.13: ユーザーの条件「掃引が1秒超えるときは欲しい」）
SHOW_ABOVE_S = 1.0
# 知らせの最短の間隔（1秒に10回まで）。制御スレッドが機器スレッドの量を読みに行く間隔も同じ
MIN_INTERVAL_S = 0.1
# 時刻の比べの許し（浮動小数の丸めで、待ち終えた時刻がわずかに手前になっても送る）
_EPS_S = 1e-9

HIDDEN = {"show": False}


class ProgressReporter:
    """機器スレッドの側。機器の口の progress_sink になり、最新の量 (世代, 済んだ量, 全体) を latest に置く。

    measure の前に begin、後に end を呼ぶ。見積もりが SHOW_ABOVE_S 以下の画面は量を置かない。
    出す画面の始まりには制御スレッドを1回起こす（量を読みに行く間隔の時計を回し始めさせる）。
    """

    def __init__(self, post: Callable[[tuple], None]) -> None:
        self._post = post
        self._gen = 0
        self.active = False  # いま出す画面を測っている
        self.latest: Optional[tuple[int, int, int]] = None

    def begin(self, gen: int, adopted: AdoptedSweep) -> None:
        self.latest = None
        self._gen = gen
        self.active = float(getattr(adopted, "estimated_sweep_s", 0.0) or 0.0) > SHOW_ABOVE_S
        if self.active:
            self._post(("sweep_progress",))

    def end(self) -> None:
        self.active = False
        self.latest = None

    def __call__(self, done: int, total: int) -> None:
        if self.active and total > 0:
            self.latest = (self._gen, max(0, min(int(done), int(total))), int(total))


class SweepProgressMixin:
    """制御スレッドの側。機器スレッドの量を読み、間引いてイベント sweep_progress にする。

    いまの世代（_gen）の掃引だけを出す。画面が更新されたら 100% にする。条件が変わる・止める・機器が仕事を
    持たない・切れたら隠す（前の世代の掃引は画面を更新しないので 100% に意味が無い）。
    """

    _gen: int

    def _init_sweep_progress(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._sp_clock = clock
        self._sp_sent: dict = dict(HIDDEN)  # 最後に送った知らせ
        self._sp_pending: Optional[dict] = None  # 間隔が明けたら送る知らせ
        self._sp_last_t = -math.inf
        self._sp_seen: Optional[tuple[int, int, int]] = None  # 最後に読んだ機器スレッドの量
        # いまの画面で出した量。これより小さい量は次の掃引のもの（機器スレッドは結果を送るとすぐ次の掃引を始めるので、
        # 制御スレッドが結果を受ける前に次の掃引の 0 を読むことがある）。画面の区切り（更新・捨てた・隠す）で 0 に戻す
        self._sp_floor = 0

    def _sweep_progress_reporter(self) -> Optional[ProgressReporter]:
        return getattr(getattr(self, "_worker", None), "progress", None)

    def _sweep_progress_poll(self) -> None:
        """機器スレッドの量を読み、変わっていれば出す。間隔が明けていれば間引いた知らせを送る（繰り返しの終わりに呼ぶ）。"""
        rep = self._sweep_progress_reporter()
        v = rep.latest if rep is not None else None
        if v is not None and v != self._sp_seen:
            if v[0] != self._gen:  # 条件が変わる前の掃引は出さない（結果は捨てる。設計B §7.2.1）
                self._sp_seen = v
            elif v[1] >= self._sp_floor:
                self._sp_seen = v
                self._sp_floor = v[1]
                self._sweep_progress_set({"show": True, "done": v[1], "total": v[2]})
            # 小さい量は次の掃引のもの: いまの画面の結果を受けて区切ってから出す（読んだことにしない）
        if self._sp_pending is not None and self._sp_clock() + _EPS_S >= self._sp_last_t + MIN_INTERVAL_S:
            self._sweep_progress_send(self._sp_pending)

    def _sweep_progress_wait(self) -> Optional[float]:
        """制御スレッドが待ち行列を待つ長さの上限。間引いた知らせを送る時刻か、出す画面を測っている間は量を
        読みに行く時刻。どちらも無ければ None。"""
        waits = []
        if self._sp_pending is not None:
            waits.append(self._sp_last_t + MIN_INTERVAL_S - self._sp_clock())
        rep = self._sweep_progress_reporter()
        if rep is not None and rep.active:
            waits.append(MIN_INTERVAL_S)
        return max(0.0, min(waits)) if waits else None

    def _sweep_progress_screen_done(self, gen: int) -> None:
        """画面が1回更新された（いまの世代の掃引の結果を受けた）: 出していれば 100% をすぐ送る（間隔によらない）。
        次の値は _sp_last_t から MIN_INTERVAL_S 待つので、100% は次の掃引の 0%・隠すに置き換わらない。"""
        if gen != self._gen:
            return
        cur = self._sp_pending or self._sp_sent
        if cur.get("show"):
            full = {"show": True, "done": cur["total"], "total": cur["total"]}
            if full != self._sp_sent:
                self._sweep_progress_send(full)
            self._sp_pending = None
        self._sp_floor = 0

    def _sweep_progress_screen_dropped(self) -> None:
        """いまの画面を捨てた（上限時間など。画面は更新されない）: 次の掃引の 0 から出す。"""
        self._sp_floor = 0

    def _sweep_progress_hide(self) -> None:
        self._sp_floor = 0
        self._sweep_progress_set(dict(HIDDEN))

    def _sweep_progress_set(self, payload: dict) -> None:
        if payload == self._sp_sent:
            self._sp_pending = None  # 送ってある知らせと同じ（間に来た別の値は要らなくなった）
            return
        if self._sp_clock() + _EPS_S >= self._sp_last_t + MIN_INTERVAL_S:
            self._sweep_progress_send(payload)
        else:
            self._sp_pending = payload

    def _sweep_progress_send(self, payload: dict) -> None:
        self._sp_pending = None
        self._sp_sent = payload
        self._sp_last_t = self._sp_clock()
        self._emit("sweep_progress", dict(payload))
