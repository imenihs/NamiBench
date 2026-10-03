"""計測器の本体（設計A §4.2）。

状態の正本は制御スレッド1本だけが触る（ロックを持たない）。
命令は待ち行列に入れて Future を返し、結果は採用値と調整の一覧で返す（設計A §4.3）。
機器との送受信は機器スレッドが行い、結果を制御スレッドの待ち行列へ戻す。
ディスクを待つ仕事は書き込みスレッドが行い、終わりを制御スレッドの待ち行列へ戻す（instrument_files.py）。
トレース・マーカー・ノーマライズ・元に戻すは instrument_display.py（1ファイル 1000 行の決まりで分けた）。
校正作業（設計K）は instrument_cal.py。
rtl_tcp の出口は instrument_rtltcp.py、RTL-SDR をつなぐ部分は instrument_rtlsdr.py、マーカーの位置を聞く
機能は instrument_listen.py。
機種のプロファイルと掃引条件モデルは、つないだ機器に合わせて切り替える（設計Q §2.1・§6.4: GigaSt と
シミュレータは GigaSt のプロファイル、RTL-SDR は sweep_kind "fft" のプロファイル）。
"""
from __future__ import annotations

import concurrent.futures
import copy
import dataclasses
import datetime
import inspect
import os
import queue
import threading
import time
import traceback
from pathlib import Path
from typing import Callable, Optional

from .devices.base import Device, load_profile
from .instrument_cal import CalMixin
from .instrument_common import CommandError, adjustments_to_list, adopted_to_dict, sweep_key
from .instrument_devices import DevicesMixin
from .instrument_diff import DiffMixin
from .instrument_display import DisplayMixin
from .instrument_files import DEFERRED, FilesMixin
from .instrument_listen import ListenMixin
from .instrument_rtlsdr import RTLSDR_ENCODING, RtlSdrMixin
from .instrument_rtltcp import RtlTcpMixin
from .instrument_view import ViewMixin
from .level.calibration import Calibration, CalibrationStore, default_root
from .level.convert import LevelConverter
from .settings import Settings
from .level.legacy_cal import LegacyCalError, parse_legacy_cal
from .sweep import image_reject as ir
from .sweep.model import SweepModel
from .trace import ConditionChange
from .types import (
    AdoptedSweep,
    DeviceDisconnected,
    DeviceError,
    InvalidSweep,
    Mode,
    RawSweep,
    SweepDiscarded,
    SweepRequest,
)

__all__ = ["Instrument", "CommandError", "DeviceBackend", "GIGAST_PROFILE_PATH", "adopted_to_dict",
           "adjustments_to_list", "app_data_root"]

GIGAST_PROFILE_PATH = Path(__file__).parent / "devices" / "gigast" / "profile.json"

# 再接続を待つ間にポートの一覧を見る間隔（設計B §7.3）
RECONNECT_POLL_S = 0.5
# 条件の変更・停止でいまの掃引の終わりを待つとき、残りがこれを超えたら進み具合を出す（設計B §7.2.1）
WAIT_PROGRESS_THRESHOLD_S = 1.0

EventCallback = Callable[[str, dict], None]


def app_data_root() -> Path:
    """利用者のデータの場所（校正は設計D §5.2、覚えた機器は設計B §7.3）。"""
    base = os.environ.get("APPDATA")
    return Path(base) / "pyGigaSt" if base else Path.home() / ".pygigast"


_sweep_key = sweep_key


class DeviceBackend:
    """機器の種類ごとの見つけ方・開き方。GigaSt とシミュレータを同じ口で扱う（設計Q §4.1）。"""

    def discover(self) -> list[dict]:
        raise NotImplementedError

    def open(self, serial: Optional[str], port: Optional[str]) -> Device:
        raise NotImplementedError

    def probe(self, port: str) -> bool:
        raise NotImplementedError


class _DeviceWorker(threading.Thread):
    """機器スレッド。渡された条件で測り続け、結果を制御スレッドへ送る。

    掃引の途中では次の要求を送らない（設計B §3・T6）。条件が変わったら cancel を立て、
    機器の口がいまの掃引の終わりを待ってから戻る。
    単発は回数をここで数えて止まる。止める指示は制御スレッドから後で届くので、ここで数えないと
    結果を返した直後に次の掃引を始めてしまい、次の操作が最長で1掃引分（約8.6s）待たされる。
    """

    def __init__(self, device: Device, post: Callable[[tuple], None]) -> None:
        super().__init__(name="pygigast-device", daemon=True)
        self.device = device
        self._post = post
        self._cv = threading.Condition()
        self._job: Optional[tuple[int, AdoptedSweep]] = None
        self._remaining: Optional[int] = None  # 単発の残りの回数。None は連続
        self._cancel = threading.Event()
        self._closing = False
        self.busy_since: Optional[float] = None

    def set_job(self, gen: int, adopted: Optional[AdoptedSweep], count: Optional[int] = None) -> None:
        with self._cv:
            self._job = None if adopted is None else (gen, adopted)
            self._remaining = count
            self._cancel.set()
            self._cv.notify_all()

    def close(self) -> None:
        with self._cv:
            self._closing = True
            self._job = None
            self._cancel.set()
            self._cv.notify_all()

    def run(self) -> None:
        while True:
            with self._cv:
                while self._job is None and not self._closing:
                    self._post(("idle",))
                    self._cv.wait()
                if self._closing:
                    break
                gen, adopted = self._job
                self._cancel = threading.Event()
                cancel = self._cancel
            self.busy_since = time.monotonic()
            try:
                raw = self.device.measure(adopted, cancel)
                with self._cv:
                    if self._job is not None and self._job[0] == gen and self._remaining is not None:
                        self._remaining -= 1
                        if self._remaining <= 0:
                            self._job = None
                self._post(("sweep", gen, raw))
            except SweepDiscarded as e:
                self._post(("discarded", gen, e.reason, e.detail))
            except DeviceDisconnected as e:
                self._post(("disconnected", str(e)))
                break
            except InvalidSweep as e:
                # 設計C で止まるはずのものが届いた（二重の防御）。送らずに止める
                self._post(("invalid", gen, str(e)))
                with self._cv:
                    if self._job is not None and self._job[0] == gen:
                        self._job = None
            except Exception as e:  # noqa: BLE001 — 機器スレッドを黙って死なせない
                self._post(("worker_error", repr(e), traceback.format_exc()))
                break
            finally:
                self.busy_since = None
        try:
            self.device.close()
        except Exception:  # noqa: BLE001
            pass
        self._post(("worker_exit",))


class Instrument(DisplayMixin, DiffMixin, FilesMixin, CalMixin, RtlTcpMixin, RtlSdrMixin, ListenMixin, DevicesMixin,
                 ViewMixin):
    """計測器の本体。API 層（api/local.py）だけがこれを使う。"""

    def __init__(
        self,
        backends: dict[str, DeviceBackend],
        profile: Optional[dict] = None,
        timing_fn=None,
        data_root: Optional[Path] = None,
        cal_root: Optional[Path] = None,
    ) -> None:
        self._backends = backends
        self._profile = profile or load_profile(GIGAST_PROFILE_PATH)
        if timing_fn is None:
            from .devices.gigast.driver import timing_fn as gigast_timing_fn

            timing_fn = gigast_timing_fn(self._profile)
        # 1回の掃引の見積もり（rtl_tcp の出口が点の時刻を出すのにも使う）
        self._timing_fn = timing_fn
        self._model = SweepModel(self._profile, timing_fn)
        # 起動時のプロファイル（GigaSt）。同じ機種の機器をつないだときはこれと見積もりをそのまま使う
        self._base_profile = self._profile
        self._base_timing_fn = timing_fn
        root = data_root or app_data_root()
        self._root = Path(root)
        self._settings = Settings(root / "settings.json")
        # 起動時は前回終了時の状態に戻る（設計O §1.2）。戻せない値は掃引条件モデルが直して知らせる
        saved = self._settings.get("state")
        self._adopted = self._restore_state(saved)
        self._cal_store = CalibrationStore(cal_root or default_root())
        self._cal: Optional[Calibration] = None
        self._converter = LevelConverter(self._profile, None)

        self._q: "queue.Queue[tuple]" = queue.Queue()
        self._subs: list[EventCallback] = []
        self._subs_lock = threading.Lock()
        self._version = 0

        # 接続: disconnected / connected / reconnecting
        self._conn = "disconnected"
        self._backend_name: Optional[str] = None
        self._device: Optional[Device] = None
        self._identity: Optional[dict] = None
        self._connected_at: Optional[float] = None
        self._worker: Optional[_DeviceWorker] = None
        self._next_reconnect = 0.0

        # 動作: stopped / continuous / single / stopping
        self._run = "stopped"
        self._gen = 0
        self._single_target = 0
        self._single_done = 0
        self._sweep_count = 0
        self._discard_count = 0
        self._last_sweep_s: Optional[float] = None
        self._last_stop_reason: Optional[str] = None

        # トレース・マーカー・振幅（前回の設定を戻す）と、書き込みスレッド・長い処理
        self._init_display(saved if isinstance(saved, dict) else None)
        # 表示の設定・配色・プリセット（設計O・J §4.4）。トレースの値を出す前に要る
        self._init_view(saved if isinstance(saved, dict) else None)
        self._init_files(saved if isinstance(saved, dict) else None)
        self._init_cal()
        self._init_diff(saved if isinstance(saved, dict) else None)  # トレースと参照トレースの後（2本を探すため）
        self._init_rtltcp()
        self._init_rtlsdr()
        self._init_listen()
        self._init_devices()

        self._thread = threading.Thread(target=self._loop, name="pygigast-control", daemon=True)
        self._running = True
        self._thread.start()
        # rtl_tcp を出すと決めていれば起動時に始める（設計Q §4.3。既定は出さない）
        self._q.put(("call", self._start_rtltcp_if_enabled))
        if self._settings.load_problem:
            # 設定が読めなかったことを1回知らせる（設計O §1.1。黙って初期値にしない）
            self._q.put(("notice", "settings_reset", self._settings.load_problem))

    def _restore_state(self, st: Optional[dict]) -> AdoptedSweep:
        base = self._model.default()
        if not st:
            return base
        try:
            req = SweepRequest(
                start_hz=int(st["start_hz"]), stop_hz=int(st["stop_hz"]),
                edited=("start_hz", "stop_hz"),
                rbw_preference_hz=st.get("rbw_preference_hz"),
                points=st.get("points_setting") or 0,
                mode=Mode(st.get("mode", "sa")),
                sg_hz=st.get("sg_hz"),
                # 前回の値。前の版の覚え書きの image_cancel は読み替え、どちらも無ければ機種の初期値（設計F §7.3）
                image_reject=ir.read_choice(st),
            )
            try:
                restored = self._model.apply(req, base)
            except ValueError:
                # 別の機種（RTL-SDR など）の RBW を覚えていた: RBW だけ既定にして範囲は戻す
                restored = self._model.apply(dataclasses.replace(req, rbw_preference_hz=None), base)
            lock = st.get("span_lock_hz")
            if lock:
                # 固定の状態は次回の起動で戻す（設計C §5.11 の 5）。固定 Span も戻す
                restored = dataclasses.replace(self._model.apply(SweepRequest(span_lock=True), restored),
                                               span_lock_hz=int(lock))
            return restored
        except (KeyError, ValueError, TypeError):
            return base

    def _state_to_save(self) -> dict:
        a = self._adopted
        return {
            "start_hz": a.start_hz, "stop_hz": a.stop_hz,
            "rbw_preference_hz": a.rbw_preference_hz, "points_setting": a.points_setting,
            "mode": a.mode.value, "sg_hz": a.sg_hz, "image_reject": a.image_reject,
            "span_lock_hz": a.span_lock_hz,
            **self._display_state_to_save(),
            **self._files_state_to_save(),
            **self._diff_state_to_save(),
            **self._view_state_to_save(),
        }

    def _error(self, code: str, field: Optional[str] = None, detail: Optional[dict] = None) -> CommandError:
        return CommandError(code, field, detail)

    # ------------------------------------------------------------------ 外への口

    def subscribe(self, cb: EventCallback) -> Callable[[], None]:
        """イベントを購読する。cb は制御スレッドから呼ばれる（画面へは API 層が渡し直す）。"""
        with self._subs_lock:
            self._subs.append(cb)

        def unsubscribe() -> None:
            with self._subs_lock:
                if cb in self._subs:
                    self._subs.remove(cb)

        return unsubscribe

    def submit(self, name: str, /, **kwargs) -> concurrent.futures.Future:
        """命令を制御スレッドの待ち行列へ入れ、Future を返す（設計A §4.2）。"""
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self._q.put(("cmd", name, kwargs, fut))
        return fut

    def shutdown(self, timeout: float = 15.0) -> None:
        fut = self.submit("_shutdown")
        try:
            fut.result(timeout)
        except Exception:  # noqa: BLE001
            pass
        self._thread.join(timeout)

    # ------------------------------------------------------------------ 制御スレッド

    def _loop(self) -> None:
        while self._running:
            # 再接続を待つ間・機器の一覧を見ている間は、0.5s ごとにポートの一覧を見る（設計B §7.3・J §4.7）
            timeout = RECONNECT_POLL_S if self._conn == "reconnecting" or self._devices_polling() else None
            try:
                item = self._q.get(timeout=timeout)
            except queue.Empty:
                item = None
            if item is not None:
                try:
                    self._handle(item)
                except Exception:  # noqa: BLE001 — 制御スレッドを止めない
                    self._notice("internal_error", {"trace": traceback.format_exc()})
            if self._conn == "reconnecting" and time.monotonic() >= self._next_reconnect:
                self._try_reconnect()
            self._poll_devices()

    def _handle(self, item: tuple) -> None:
        kind = item[0]
        if kind == "cmd":
            _, name, kwargs, fut = item
            if not fut.set_running_or_notify_cancel():
                return
            fn = getattr(self, "_cmd_" + name, None)
            self._cur_fut = fut
            try:
                if fn is None:
                    raise CommandError("unknown_command", None, {"name": name})
                # 引数の名前と数が命令に合わないときだけ「操作の値が正しくありません」。命令の中で起きた型の誤りまで
                # これにすると、正しい値を入れた利用者に入力を疑わせた（2026-10-03 ユーザー指摘「絶対におかしくない
                # はずなのに弾かれる」）。中の誤りは内部の失敗として中身を付けて返し、記録にも残す
                try:
                    inspect.signature(fn).bind(**kwargs)
                except TypeError as e:
                    raise CommandError("bad_arguments", None, {"message": str(e)}) from None
                res = fn(**kwargs)
                if res is not DEFERRED:  # 書き込みスレッドの仕事が終わってから結果を入れる
                    fut.set_result(res)
            except CommandError as e:
                fut.set_exception(e)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()  # 中身は標準エラーにも残す（ほかの内部の失敗と同じ）
                fut.set_exception(CommandError("internal_error", None,
                                               {"message": f"{type(e).__name__}: {e}", "command": name}))
            finally:
                self._cur_fut = None
        elif kind == "call":
            item[1]()
        elif kind == "sweep":
            self._on_sweep(item[1], item[2])
        elif kind == "discarded":
            self._on_discarded(item[1], item[2], item[3])
        elif kind == "disconnected":
            self._on_disconnected(item[1])
        elif kind == "invalid":
            self._notice("invalid_sweep", {"message": item[2]})
            if self._cal_job is not None:
                self._cal_abort(CommandError("invalid_sweep", None, {"message": item[2]}))
            if self._ref_job is not None:
                self._abort_ref_job(CommandError("invalid_sweep", None, {"message": item[2]}))
            self._set_run("stopped", reason="invalid_sweep")
        elif kind == "worker_error":
            self._notice("device_error", {"message": item[1], "trace": item[2]})
            self._drop_device()
            self._set_conn("disconnected")
        elif kind == "idle":
            if self._run == "stopping":
                self._set_run("stopped")
        elif kind == "worker_exit":
            pass
        elif kind == "notice":
            self._notice(item[1], item[2])

    # ---- 状態とイベント

    def _emit(self, kind: str, payload: dict) -> None:
        with self._subs_lock:
            subs = list(self._subs)
        for cb in subs:
            try:
                cb(kind, payload)
            except Exception:  # noqa: BLE001 — 画面側の失敗で制御スレッドを止めない
                traceback.print_exc()

    def _changed(self) -> None:
        self._version += 1
        self._emit("state", self._snapshot())

    def _notice(self, code: str, detail: Optional[dict] = None) -> None:
        self._emit("notice", {"code": code, "detail": detail or {}, "time": time.time()})

    def _snapshot(self) -> dict:
        cal = None
        if self._cal is not None:
            cal = {"created_at": self._cal.created_at, "source": dataclasses.asdict(self._cal.source)}
        return {
            "version": self._version,
            "connection": {
                "state": self._conn,
                "backend": self._backend_name,
                "identity": self._identity,
                "connected_at": self._connected_at,
                "profile_model": self._profile.get("model"),
                "display_name": self._profile.get("display_name"),
                "sweep_kind": self._profile.get("sweep_kind", "stepped"),
                "name": self._current_device_name(),  # 利用者が付けた名前か既定の名前（設計J §4.7）
            },
            "sweep": adopted_to_dict(self._adopted),
            "run": {
                "state": self._run,
                "single_target": self._single_target,
                "single_done": self._single_done,
                "sweep_count": self._sweep_count,
                "discard_count": self._discard_count,
                "last_sweep_s": self._last_sweep_s,
                "last_stop_reason": self._last_stop_reason,
            },
            "calibration": cal,
            # 校正の画面の作業（設計K §4.1。開いていなければ None）
            "calibration_work": self._cal_state(),
            "source": self._source_state(),
            **self._display_snapshot(),
            "files": self._files_state(),
            "difference": self._diff_state(),
            "recording": self._recording_state(),
            "tasks": self._tasks_state(),
            "rtltcp": self._rtltcp_state(),
            "rtlsdr": self._rtlsdr_state(),
            "listen": self._listen_state(),
            **self._view_snapshot(),
        }

    # 信号源を出すモード（設計L §1.1）。S11 は機器から見れば TG と同じ掃引
    _SOURCE_OF_MODE = {Mode.TG: "tg", Mode.S11: "tg", Mode.SG: "sg"}

    def _source_state(self) -> dict:
        """いま機器が TG・SG を出しているか（設計L: 出ていることが画面で分かるようにする）。
        出しているとみなすのは、つないでいて掃引を機器へ渡している間（連続・単発・止まる途中・基準を取る間）だけ。
        掃引を止めた後も機器が出し続けるかは設計に書かれていないので、止めたら出していないとして出す。"""
        job = self._ref_job
        cal = self._cal_source()
        if cal is not None:
            adopted, sweeping = cal, True  # 校正の掃引（RBW の校正は SG で 500MHz を出す: 設計K §4.3）
        elif job is not None:
            adopted, sweeping = job.adopted, True
        else:
            adopted, sweeping = self._adopted, self._run in ("continuous", "single", "stopping")
        output = self._SOURCE_OF_MODE.get(adopted.mode, "none")
        src = self._profile.get("source") or {}
        if self._conn != "connected" or not sweeping or not src.get(output):
            output = "none"
        return {"output": output, "freq_hz": adopted.sg_hz if output == "sg" else None}

    def _set_conn(self, state: str) -> None:
        self._conn = state
        self._changed()

    def _set_run(self, state: str, reason: Optional[str] = None) -> None:
        self._run = state
        if reason is not None:
            self._last_stop_reason = reason
        self._changed()

    # ---- 機器スレッドへの仕事の渡し方

    def _post(self, item: tuple) -> None:
        self._q.put(item)

    def _push_job(self) -> None:
        """いまの動作と条件を機器スレッドへ渡す。前の条件の掃引の結果は世代で見分けて捨てる。"""
        self._gen += 1
        if self._worker is None:
            return
        busy = self._worker.busy_since
        if self._cal_push_job():
            return  # 校正の間は、いまの条件とは別の条件で決めた回数だけ測る（設計K §4.1）
        job = self._ref_job
        if job is not None:
            # 基準を取っている間は、いまの条件とは別の条件で決めた回数だけ測る（設計E §6.2）
            count = max(0, job.count - len(job.frames))
            wanted = self._conn == "connected" and count > 0
            self._worker.set_job(self._gen, job.adopted if wanted else None, count)
            return
        wanted = self._run in ("continuous", "single") and self._conn == "connected"
        count = max(0, self._single_target - self._single_done) if self._run == "single" else None
        if count == 0:
            wanted = False
        self._worker.set_job(self._gen, self._adopted if wanted else None, count)
        if busy is not None:
            remaining = self._adopted_estimate_remaining(busy)
            if remaining > WAIT_PROGRESS_THRESHOLD_S:
                self._emit("progress", {"kind": "waiting_sweep_end", "remaining_s": remaining})

    def _adopted_estimate_remaining(self, busy_since: float) -> float:
        # いま走っている掃引の条件は前の世代だが、見積もりは直前の採用値で足りる（表示だけに使う）
        return max(0.0, self._last_estimate - (time.monotonic() - busy_since))

    _last_estimate = 0.0

    # ---- 掃引の結果

    def _on_sweep(self, gen: int, raw: RawSweep) -> None:
        if gen != self._gen:
            return  # 条件が変わる前の掃引（設計B §7.2.1: 結果は捨てる）
        self._last_sweep_s = raw.duration_s
        if self._cal_job is not None:
            # 校正のための掃引。トレース・記録には入れない（設計K §4.1）
            self._cal_on_sweep(raw)
            return
        if self._ref_job is not None:
            # 基準を取るための掃引。トレース・記録には入れない（設計E §6.2）
            self._on_ref_sweep(raw)
            return
        self._sweep_count += 1
        frame = self._build_frame(raw)
        self._rtltcp_on_sweep(raw, frame)  # 相手がいなければ何もしない（設計Q §4.3）
        self._last_raw = raw
        self._process_frame(frame)
        # 記録は書き込みスレッドへ渡すだけで待たない（設計H §2・§7.2）
        self._record_sweep(raw, self._last_quantity, frame.value_kind)
        if self._run == "single":
            self._single_done += 1
            if self._single_done >= self._single_target:
                self._set_run("stopped", reason="single_done")
                self._push_job()
                return
        self._changed()

    def _on_discarded(self, gen: int, reason: str, detail: dict) -> None:
        if reason == "cancelled":
            return  # 利用者の操作で捨てた掃引。数えない
        self._discard_count += 1
        self._notice("sweep_discarded", {"reason": reason, **(detail or {})})
        self._changed()

    # ---- 接続・切断・再接続（設計B §7.3）

    def _attach(self, device: Device, backend: str) -> None:
        self._device = device
        self._backend_name = backend
        ident = device.identity()
        self._identity = {"model": ident.model, "serial": ident.serial, "port": ident.port}
        # 接続＝電源の入れ直し（USB 給電）。時刻を持つ（設計B §7.2.1）
        self._connected_at = time.time()
        # 機種のプロファイルと掃引条件モデルを、つないだ機器のものにする（設計Q §2.1）
        self._activate_profile(device)
        if self._is_rtlsdr():
            # 個体の校正（ppm）と利得は、最初の測定より前に入れる（設計Q §6.6）
            self._rtlsdr_prepare(device)
        self._worker = _DeviceWorker(device, self._post)
        self._worker.start()
        self._load_calibration(ident.serial)
        self._load_saved_reference()
        self._conn = "connected"
        self._cal_refresh()  # 校正の画面が開いていれば、つないだ機器の校正を「前の値」にする（つないだ後に見る）
        self._rtltcp_update_header()
        self._rtlsdr_on_clients()  # rtl_tcp の相手が先につながっていれば、中心を動かさない動きにする
        self._push_job()
        self._changed()

    def _timing_for(self, profile: dict):
        """機種のプロファイルに合う1回の掃引の見積もり（機器の層が作る: 設計C §5・設計Q §6.4）。"""
        if profile.get("model") == self._base_profile.get("model"):
            return self._base_timing_fn
        if (profile.get("raw") or {}).get("encoding") == RTLSDR_ENCODING:
            from .devices.rtlsdr.driver import timing_fn as rtlsdr_timing_fn

            return rtlsdr_timing_fn(profile)
        from .devices.gigast.driver import timing_fn as gigast_timing_fn

        return gigast_timing_fn(profile)

    def _activate_profile(self, device: Device) -> None:
        """つないだ機器の機種のプロファイルへ切り替える。

        同じ機種なら（GigaSt・シミュレータは起動時のプロファイルのまま）条件を変えない。機種が変わったら、
        いまの条件を新しい掃引条件モデルに通し直して調整を知らせ、トレースの蓄積を捨てる（モードが変わるのと
        同じ扱い: 設計F §5.3）。マーカーは残す。元に戻すの履歴は別の機種の条件なので捨てる。
        """
        try:
            prof = device.profile() or self._profile
        except Exception:  # noqa: BLE001 — 読めなければいまのプロファイルのまま
            return
        # 機器から読んだプロファイルは写して持つ（機器の層が持つ辞書を本体の状態にしない）
        target = self._base_profile if prof.get("model") == self._base_profile.get("model") else copy.deepcopy(prof)
        if target.get("model") == self._profile.get("model"):
            if target is not self._base_profile:
                self._profile = target  # 同じ機種でも機器から読んだ値（利得の段など）は新しくする
            return
        self._listen_end("device_changed", restore=False)
        old_model = self._profile.get("model")
        old = self._adopted
        self._profile = target
        self._timing_fn = self._timing_for(target)
        self._model = SweepModel(target, self._timing_fn)
        new, extra_adj = self._reapply_conditions(old)
        self._undo.clear()
        self._redo.clear()
        self._adopted = new
        self._last_estimate = new.estimated_sweep_s
        self._on_condition(ConditionChange.MODE)
        self._notice("device_profile_changed", {
            "model": target.get("model"), "previous_model": old_model,
            "sweep_kind": target.get("sweep_kind", "stepped"),
            "adopted": adopted_to_dict(new),
            "adjustments": extra_adj + adjustments_to_list(new),
        })

    def _reapply_conditions(self, old: AdoptedSweep) -> tuple[AdoptedSweep, list[dict]]:
        """前の機種の条件を新しい掃引条件モデルに通す。

        範囲が新しい機種の範囲に収まらないときは、新しい機種の既定の範囲にする（RTL-SDR は範囲いっぱいだと
        1回の測定に約 117s かかる: 設計Q §6.9）。RBW が選択肢に無いときは既定にする。
        """
        base = self._model.default()
        f = self._profile["freq"]
        adj: list[dict] = []
        start, stop = old.start_hz, old.stop_hz
        if not (int(f["min_hz"]) <= start < stop <= int(f["max_hz"])):
            adj.append({"field": "start_hz", "requested": start, "adopted": base.start_hz,
                        "reason": "device_default_range"})
            adj.append({"field": "stop_hz", "requested": stop, "adopted": base.stop_hz,
                        "reason": "device_default_range"})
            start, stop = base.start_hz, base.stop_hz
        req = SweepRequest(start_hz=start, stop_hz=stop, edited=("start_hz", "stop_hz"),
                           rbw_preference_hz=old.rbw_preference_hz, mode=old.mode,
                           image_reject=old.image_reject)
        try:
            new = self._model.apply(req, base)
        except (ValueError, TypeError):
            adj.append({"field": "rbw_hz", "requested": old.rbw_hz, "adopted": base.rbw_hz,
                        "reason": "device_changed"})
            try:
                new = self._model.apply(dataclasses.replace(req, rbw_preference_hz=None), base)
            except (ValueError, TypeError):
                new = base
        return new, adj

    def _drop_device(self) -> None:
        if self._cal_job is not None:
            self._cal_abort(CommandError("disconnected"))
        self._listen_end("device_lost")
        self._rtlsdr_on_drop()
        if self._worker is not None:
            self._worker.close()
            self._worker = None
        self._device = None

    def _on_disconnected(self, message: str) -> None:
        self._drop_device()
        if self._ref_job is not None:
            self._abort_ref_job(CommandError("disconnected", None, {"message": message}))
        self._notice("disconnected", {"message": message})
        self._next_reconnect = time.monotonic() + RECONNECT_POLL_S
        self._set_conn("reconnecting")

    def _try_reconnect(self) -> None:
        self._next_reconnect = time.monotonic() + RECONNECT_POLL_S
        backend = self._backends.get(self._backend_name or "")
        serial = (self._identity or {}).get("serial")
        if backend is None or serial is None:
            return
        try:
            ports = backend.discover()
        except Exception:  # noqa: BLE001
            return
        matches = [p for p in ports if p.get("serial") == serial and p.get("available", True)]
        if len(matches) != 1:
            # 見つからない・同じシリアル番号が複数（RTL-SDR: どれが元の機器か分からない）なら繋がない（設計Q §6.6）
            return
        try:
            device = backend.open(serial, None)
        except (DeviceError, OSError):
            return
        self._notice("reconnected", {"serial": serial})
        # 直前の条件で掃引を再開する（T8: 待ち時間なしで正常に戻る）
        self._attach(device, self._backend_name)

    def _load_calibration(self, serial: str) -> None:
        if (self._profile.get("raw") or {}).get("encoding") == RTLSDR_ENCODING:
            # RTL-SDR の個体の校正は _rtlsdr_prepare で読んだ（GigaSt の校正の形は使わない）
            self._cal = None
            return
        old = None if self._cal is None else self._cal.to_dict()
        try:
            self._cal = self._cal_store.load_current(serial)
        except Exception as e:  # noqa: BLE001 — 読めない校正は「無し」として知らせる
            self._cal = None
            self._notice("calibration_unreadable", {"message": str(e)})
        self._converter = LevelConverter(self._profile, self._cal)
        if (None if self._cal is None else self._cal.to_dict()) != old:
            self._on_condition(ConditionChange.CALIBRATION)  # 値の意味が変わる（設計F §5.3）
        if self._cal is None:
            self._notice("no_calibration", {"serial": serial})

    # ------------------------------------------------------------------ 命令

    def _cmd__shutdown(self) -> None:
        self._shutdown_rtltcp()
        self._drop_device()
        self._shutdown_files()
        self._settings.set("state", self._state_to_save())
        self._save_palette_setting()
        try:
            self._settings.save()
        except OSError as e:
            self._notice("settings_unwritable", {"message": str(e)})
        self._running = False

    def _cmd_get_state(self) -> dict:
        return self._snapshot()

    def _set_adopted(self, new: AdoptedSweep, step_sweep: bool = False) -> None:
        """採用値を置き換える。値の意味が変わる条件ならトレースの蓄積を捨てる（設計F §5.3）。

        step_sweep は SG のステップ掃引（設計L。まだ無い）が SG の周波数を動かすときに True で呼ぶ。最大・最小
        保持を選択によらず残す（設計F §8 の 2）。利用者の操作（set_mode・set_sweep・元に戻す・プリセット）は False。
        """
        old = self._adopted
        # 機器へ送る中身が変わったときだけ掃引をやり直す（固定の切り替え・知らせだけの違いではやり直さない）
        changed = _sweep_key(new) != _sweep_key(old)
        self._adopted = new
        self._last_estimate = new.estimated_sweep_s
        if changed:
            self._on_sweep_condition(old, new, step_sweep=step_sweep)
            if self._run == "single":
                self._single_done = 0
            self._push_job()
        self._changed()

    def _apply(self, request: SweepRequest) -> dict:
        """条件を変える（set_sweep・set_mode・マーカーから）。元に戻せるように前の条件を積む（設計J §3）。"""
        self._check_not_taking_reference()
        before = self._undo_snapshot()
        try:
            new = self._model.apply(request, self._adopted)
        except (ValueError, TypeError) as e:
            raise CommandError("bad_value", None, {"message": str(e)}) from e
        # RTL-SDR の中心を動かせない間は、継ぎ合わせの要る条件を受け付けない（設計Q §6.7）
        self._rtlsdr_check_condition(new)
        self._set_adopted(new)
        self._record_undo(before)
        out = {"adopted": adopted_to_dict(new), "adjustments": adjustments_to_list(new)}
        if new.alternative is not None:
            # 反対の規則で作った結果。画面はこれを「その1回だけ」のボタンにする（設計C §5.11 の 3・4）
            out["alternative"] = dataclasses.asdict(new.alternative)
        return out

    def _cmd_set_sweep(
        self,
        start_hz: Optional[int] = None,
        stop_hz: Optional[int] = None,
        center_hz: Optional[int] = None,
        span_hz: Optional[int] = None,
        edited: Optional[list[str]] = None,
        rbw_hz: Optional[int] = None,
        points: Optional[int] = None,
        points_auto: bool = False,
        image_reject: Optional[str] = None,
        span_lock: Optional[bool] = None,
        one_shot: bool = False,
    ) -> dict:
        for name, v in (("start_hz", start_hz), ("stop_hz", stop_hz), ("center_hz", center_hz),
                        ("span_hz", span_hz), ("rbw_hz", rbw_hz), ("points", points)):
            if v is not None and (isinstance(v, bool) or not isinstance(v, int)):
                raise CommandError("not_integer", name)
        if image_reject is not None and image_reject not in ir.CHOICES:
            raise CommandError("bad_value", "image_reject", {"choices": list(ir.CHOICES)})
        if edited is None:
            edited = [n for n, v in (("start_hz", start_hz), ("stop_hz", stop_hz), ("center_hz", center_hz),
                                     ("span_hz", span_hz)) if v is not None]
        req = SweepRequest(
            start_hz=start_hz, stop_hz=stop_hz, center_hz=center_hz, span_hz=span_hz,
            edited=tuple(edited), rbw_preference_hz=rbw_hz,
            points=points, image_reject=image_reject,
            span_lock=span_lock, one_shot=bool(one_shot),
        )
        if points_auto:
            req = dataclasses.replace(req, points=0)
        return self._apply(req)

    def _cmd_set_mode(self, mode: str, sg_hz: Optional[int] = None) -> dict:
        try:
            m = Mode(mode)
        except ValueError as e:
            raise CommandError("unknown_mode", "mode", {"mode": mode}) from e
        if sg_hz is not None and (isinstance(sg_hz, bool) or not isinstance(sg_hz, int)):
            raise CommandError("not_integer", "sg_hz")
        return self._apply(SweepRequest(mode=m, sg_hz=sg_hz))

    def _cmd_run(self, mode: str = "continuous", count: Optional[int] = None) -> dict:
        if self._conn != "connected":
            raise CommandError("not_connected")
        self._check_not_taking_reference()
        if mode == "continuous":
            self._run = "continuous"
        elif mode == "single":
            # 回数を持つトレースのうち最大の N 回、無ければ1回。1回目の前にそれらの蓄積を捨てる（設計F §5.2.1）。
            # count は回数を直接決めたいとき（試験・将来の用途）だけ
            self._run = "single"
            self._single_target = max(1, int(count)) if count else self._traces.single_target()
            self._traces.reset_for_single()
            self._single_done = 0
        else:
            raise CommandError("unknown_run_mode", "mode", {"mode": mode})
        self._last_stop_reason = None
        self._push_job()
        self._changed()
        return {"run": self._run}

    def _cmd_stop(self) -> dict:
        """いまの掃引の終わりを待ってから止まる（設計B §3）。"""
        if self._run in ("continuous", "single"):
            self._run = "stopping" if (self._worker and self._worker.busy_since) else "stopped"
            self._last_stop_reason = "user"
            self._push_job()
            self._changed()
        return {"run": self._run}

    def _cmd_import_legacy_calibration(self, path: str, serial: Optional[str] = None) -> dict:
        """旧校正ファイルを取り込む（設計D §5.1・§5.2・§5.6）。どの機器の校正かは呼ぶ側が選ぶ（既定は接続中）。"""
        serial = serial or (self._identity or {}).get("serial")
        if not serial:
            raise CommandError("device_not_selected", "serial")
        if (self._profile.get("raw") or {}).get("encoding") == RTLSDR_ENCODING \
                and (self._identity or {}).get("serial") == serial:
            # 旧校正ファイルは GigaSt の形（設計D §5.1）。RTL-SDR の校正は rtlsdr_calibrate_* で取る
            raise CommandError("legacy_calibration_not_for_device", "serial", {"model": self._profile.get("model")})
        p = Path(path)
        try:
            data = p.read_bytes()
        except OSError as e:
            raise CommandError("file_unreadable", "path", {"message": str(e)}) from e
        try:
            legacy = parse_legacy_cal(data)
        except LegacyCalError as e:
            raise CommandError("legacy_unreadable", "path", {"line": e.line, "field": e.field, "reason": e.reason}) from e
        import hashlib

        cal = Calibration.from_legacy(
            legacy,
            model=self._profile["model"],
            serial=serial,
            file_name=p.name,
            sha256=hashlib.sha256(data).hexdigest(),
            created_at=datetime.datetime.now().astimezone(),
        )
        self._cal_store.save(cal, make_current=True)
        if (self._identity or {}).get("serial") == serial:
            self._cal = cal
            self._converter = LevelConverter(self._profile, cal)
            self._on_condition(ConditionChange.CALIBRATION)  # 校正が変わると値の意味が変わる（設計F §5.3）
            self._push_job()
        self._cal_refresh()
        self._changed()
        return {"summary": cal.to_dict()}
