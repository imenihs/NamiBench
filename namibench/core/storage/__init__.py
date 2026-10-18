"""測定データの保存・読込・書き出し（設計H）。

- 記録中は SQLite の作業ファイル（recording.py）、保存ファイルは HDF5（h5file.py）、
  他のアプリへは範囲を選んで CSV・Touchstone に書き出す（export.py）
- ディスクを待つ仕事はすべて書き込みスレッド（WriterThread、1本）で行う（設計A §4.2）。
  制御スレッド・画面スレッドはディスクを待たない
- 時間がかかる仕事は進み具合 (済んだ数, 全体の数, 経過秒) を知らせ、やめられる（設計H §4.1）
- 周波数はファイルの中も Hz の int64（設計A §2.1）

ここには3つのモジュールが共有するもの（例外・掃引条件の JSON・書き込みスレッド）だけを置く。
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import queue
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from ..sweep import image_reject as ir
from ..types import Adjustment, AdoptedSweep, FreqAxis, Mode

# 進み具合の知らせ: (済んだ数, 全体の数, 経過秒)。書き込みスレッドから呼ぶ（設計H §4.1）
ProgressFn = Callable[[int, int, float], None]

# 掃引条件の JSON（設計C §5.8）の版。読むときに知らない版は開かずに理由を出す（設計H §7.1）。
# 2 でイメージキャンセル（真偽 image_cancel）をイメージ除去（image_reject）に置き換えた（設計F §7.3）。
# 1 は読むときに true → "fast"、false → "off" に読み替える
CONDITIONS_VERSION = 2
READABLE_CONDITIONS_VERSIONS = (1, 2)


class StorageError(Exception):
    """保存・読込・書き出しの失敗。code は理由の種類のコード（画面の文言は画面側: U-22）。"""

    def __init__(self, code: str, detail: Optional[dict] = None) -> None:
        super().__init__(code if not detail else f"{code}: {detail}")
        self.code = code
        self.detail = detail or {}


class Cancelled(StorageError):
    """利用者がやめた。書きかけのファイルは消してある（設計H §4.1）。"""

    def __init__(self, detail: Optional[dict] = None) -> None:
        super().__init__("cancelled", detail)


class Progress:
    """進み具合の知らせとやめる印をまとめたもの。

    知らせは 0 件目・終わり・その間は最短 interval_s ごとに出す（多すぎる知らせで制御スレッドを埋めない）。
    """

    def __init__(
        self,
        total: int,
        callback: Optional[ProgressFn] = None,
        cancel: Optional[threading.Event] = None,
        interval_s: float = 0.1,
    ) -> None:
        self.total = int(total)
        self._cb = callback
        self._cancel = cancel
        self._interval = interval_s
        self._t0 = time.monotonic()
        self._last = -1.0

    def check(self) -> None:
        """やめる印が立っていたら Cancelled を投げる（後片付けは呼び側の finally）。"""
        if self._cancel is not None and self._cancel.is_set():
            raise Cancelled()

    def report(self, done: int, force: bool = False) -> None:
        if self._cb is None:
            return
        now = time.monotonic()
        if force or done >= self.total or now - self._last >= self._interval:
            self._last = now
            self._cb(int(done), self.total, now - self._t0)


# ---------------------------------------------------------------------------
# 掃引条件（採用済み。設計C §5.8）と JSON
# ---------------------------------------------------------------------------


def _json_default(o: Any) -> Any:
    # 採用値・求めた値には enum や numpy の整数が入りうる。周波数を float にしない（設計A §2.1）
    if hasattr(o, "item"):
        return o.item()
    if isinstance(o, (set, frozenset, tuple)):
        return list(o)
    return str(o)


def dumps(obj: Any) -> str:
    """属性・メタに書く JSON。ASCII だけにする（CSV の先頭行を文字コードに依らず読めるように）。"""
    return json.dumps(obj, ensure_ascii=True, sort_keys=True, default=_json_default, separators=(",", ":"))


def adopted_to_dict(a: AdoptedSweep) -> dict:
    """採用済みの掃引条件を JSON にできる dict にする（版付き）。"""
    return {
        "version": CONDITIONS_VERSION,
        "start_hz": int(a.start_hz),
        "stop_hz": int(a.stop_hz),
        "axis": {"start_hz": int(a.axis.start_hz), "step_hz": int(a.axis.step_hz), "points": int(a.axis.points)},
        "rbw_hz": int(a.rbw_hz),
        "rbw_preference_hz": int(a.rbw_preference_hz),
        "mode": Mode(a.mode).value,
        "sg_hz": None if a.sg_hz is None else int(a.sg_hz),
        "image_reject": str(a.image_reject),
        "preroll_points": int(a.preroll_points),
        "transient_head_points": int(a.transient_head_points),
        "estimated_sweep_s": float(a.estimated_sweep_s),
        "adjustments": [
            {"field": j.field, "requested": j.requested, "adopted": j.adopted, "reason": j.reason}
            for j in a.adjustments
        ],
        "points_setting": a.points_setting,
        "tail_gap": bool(a.tail_gap),
        "warnings": list(a.warnings),
        # 1点あたりの時間の見積もり（秒）。ゼロスパンの区間の各点の時刻 = 掃引の開始時刻 +（前置き + 点番号）× これ
        # （設計N §1。機器が時刻を返さないので推定）。前の版の条件には無い（読むときは 0）
        "point_time_s": float(a.point_time_s),
    }


def adopted_from_dict(d: dict) -> AdoptedSweep:
    """adopted_to_dict の逆。知らない版は StorageError("unsupported_version")。前の版のイメージキャンセルは読み替える。"""
    ver = d.get("version")
    if ver not in READABLE_CONDITIONS_VERSIONS:
        raise StorageError("unsupported_version", {"what": "conditions", "version": ver})
    try:
        reject = ir.read_choice(d) or ir.OFF
    except ValueError as e:
        raise StorageError("unsupported_version", {"what": "conditions", "image_reject": d.get("image_reject")}) from e
    ax = d["axis"]
    return AdoptedSweep(
        start_hz=int(d["start_hz"]),
        stop_hz=int(d["stop_hz"]),
        axis=FreqAxis(int(ax["start_hz"]), int(ax["step_hz"]), int(ax["points"])),
        rbw_hz=int(d["rbw_hz"]),
        rbw_preference_hz=int(d["rbw_preference_hz"]),
        mode=Mode(d["mode"]),
        sg_hz=None if d.get("sg_hz") is None else int(d["sg_hz"]),
        image_reject=reject,
        preroll_points=int(d["preroll_points"]),
        transient_head_points=int(d["transient_head_points"]),
        estimated_sweep_s=float(d["estimated_sweep_s"]),
        adjustments=tuple(Adjustment(j["field"], j["requested"], j["adopted"], j["reason"]) for j in d["adjustments"]),
        points_setting=d.get("points_setting"),
        tail_gap=bool(d.get("tail_gap", False)),
        warnings=tuple(d.get("warnings", ())),
        point_time_s=float(d.get("point_time_s") or 0.0),
    )


def points_within_stop(a: AdoptedSweep) -> int:
    """保存する点数: 機器が測った点のうち、指定の Stop までの点（設計C §5.3: 保存・書き出しは Start〜Stop で切る）。

    ゼロスパン（Step 0）は全点が同じ周波数なので全点。
    """
    ax = a.axis
    if ax.step_hz == 0:
        return ax.points
    n = (a.stop_hz - ax.start_hz) // ax.step_hz + 1
    return max(1, min(ax.points, n))


# ---------------------------------------------------------------------------
# 途中のファイルを残さない書き方（一時ファイル → 名前の付け替え。設計H §2）
# ---------------------------------------------------------------------------


def temp_path_for(path: Path) -> Path:
    """書きかけの一時ファイルの名前（同じフォルダ。付け替えを同じボリュームの中で行うため）。"""
    return path.with_name(path.name + ".partial")


def remove_quietly(path: Path) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# 書き込みスレッド（設計A §4.2: 記録・保存・書き出しを1本で順に行う）
# ---------------------------------------------------------------------------


class WriterThread:
    """待ち行列で仕事を受け、1本のスレッドで順に実行する。

    submit は待たずに Future（結果を後で受け取る札）を返す。ディスクを待つのはこのスレッドだけ。
    仕事は入れた順に行うので、記録の追記 → 記録の終了 → HDF5 へのまとめ、の順が崩れない。
    """

    def __init__(self, name: str = "namibench-writer") -> None:
        self._q: "queue.Queue[Optional[tuple]]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._closed = False
        self._thread.start()

    def submit(self, fn: Callable, *args: Any, **kwargs: Any) -> concurrent.futures.Future:
        if self._closed:
            raise RuntimeError("書き込みスレッドは終了済みです")
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self._q.put((fut, fn, args, kwargs))
        return fut

    def pending(self) -> int:
        """待ち行列に残っている仕事の数（記録中の状態表示・閉じるときの確認に使う）。"""
        return self._q.qsize()

    def in_writer_thread(self) -> bool:
        return threading.current_thread() is self._thread

    def close(self, timeout: Optional[float] = None) -> None:
        """入れ終わった仕事を全部済ませてから止まる。"""
        if self._closed:
            return
        self._closed = True
        self._q.put(None)
        self._thread.join(timeout)

    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            fut, fn, args, kwargs = item
            if not fut.set_running_or_notify_cancel():
                continue
            try:
                fut.set_result(fn(*args, **kwargs))
            except BaseException as e:  # 仕事の失敗でスレッドを止めない
                fut.set_exception(e)
