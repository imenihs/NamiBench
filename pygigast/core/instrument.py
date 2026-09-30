"""計測器の本体（設計A §4.2）。

状態の正本は制御スレッド1本だけが触る（ロックを持たない）。
命令は待ち行列に入れて Future を返し、結果は採用値と調整の一覧で返す（設計A §4.3）。
機器との送受信は機器スレッドが行い、結果を制御スレッドの待ち行列へ戻す。
"""
from __future__ import annotations

import concurrent.futures
import dataclasses
import datetime
import json
import os
import queue
import threading
import time
import traceback
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .devices.base import Device, load_profile
from .level.calibration import Calibration, CalibrationStore, default_root
from .level.convert import LevelConverter
from .settings import Settings
from .level.legacy_cal import LegacyCalError, parse_legacy_cal
from .sweep.model import SweepModel
from .types import (
    AdoptedSweep,
    DeviceDisconnected,
    InvalidSweep,
    Mode,
    RawSweep,
    SweepDiscarded,
    SweepRequest,
)

GIGAST_PROFILE_PATH = Path(__file__).parent / "devices" / "gigast" / "profile.json"

# 再接続を待つ間にポートの一覧を見る間隔（設計B §7.3）
RECONNECT_POLL_S = 0.5
# 条件の変更・停止でいまの掃引の終わりを待つとき、残りがこれを超えたら進み具合を出す（設計B §7.2.1）
WAIT_PROGRESS_THRESHOLD_S = 1.0

EventCallback = Callable[[str, dict], None]


class CommandError(Exception):
    """命令の失敗。code は理由の種類。画面の文言は画面側が決める（設計A §4.3）。"""

    def __init__(self, code: str, field: Optional[str] = None, detail: Optional[dict] = None) -> None:
        super().__init__(code)
        self.code = code
        self.field = field
        self.detail = detail or {}

    def to_dict(self) -> dict:
        return {"error": {"code": self.code, "field": self.field, "detail": self.detail}}


def app_data_root() -> Path:
    """利用者のデータの場所（校正は設計D §5.2、覚えた機器は設計B §7.3）。"""
    base = os.environ.get("APPDATA")
    return Path(base) / "pyGigaSt" if base else Path.home() / ".pygigast"


def adopted_to_dict(a: AdoptedSweep) -> dict:
    """採用値を JSON に写せる形にする（周波数は Hz の整数のまま）。"""
    return {
        "start_hz": a.start_hz,
        "stop_hz": a.stop_hz,
        "center_hz": a.center_hz,
        "span_hz": a.span_hz,
        "step_hz": a.axis.step_hz,
        "points": a.axis.points,
        "points_setting": a.points_setting,
        "rbw_hz": a.rbw_hz,
        "rbw_preference_hz": a.rbw_preference_hz,
        "mode": a.mode.value,
        "sg_hz": a.sg_hz,
        "image_cancel": a.image_cancel,
        "preroll_points": a.preroll_points,
        "transient_head_points": a.transient_head_points,
        "estimated_sweep_s": a.estimated_sweep_s,
        "tail_gap": a.tail_gap,
        "warnings": list(a.warnings),
        "span_lock_hz": a.span_lock_hz,
    }


def _sweep_key(a: AdoptedSweep) -> tuple:
    """機器へ送る中身（これが同じなら測り直さなくてよい）。"""
    return (a.start_hz, a.stop_hz, a.axis, a.rbw_hz, a.mode, a.sg_hz, a.image_cancel, a.preroll_points)


def adjustments_to_list(a: AdoptedSweep) -> list[dict]:
    return [dataclasses.asdict(x) for x in a.adjustments]


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


class Instrument:
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
        self._model = SweepModel(self._profile, timing_fn)
        root = data_root or app_data_root()
        self._settings = Settings(root / "settings.json")
        # 起動時は前回終了時の状態に戻る（設計O §1.2）。戻せない値は掃引条件モデルが直して知らせる
        self._adopted = self._restore_state(self._settings.get("state"))
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

        self._thread = threading.Thread(target=self._loop, name="pygigast-control", daemon=True)
        self._running = True
        self._thread.start()
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
                image_cancel=bool(st.get("image_cancel", False)),
            )
            restored = self._model.apply(req, base)
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
            "mode": a.mode.value, "sg_hz": a.sg_hz, "image_cancel": a.image_cancel,
            "span_lock_hz": a.span_lock_hz,
        }

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

    def submit(self, name: str, **kwargs) -> concurrent.futures.Future:
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
            timeout = RECONNECT_POLL_S if self._conn == "reconnecting" else None
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

    def _handle(self, item: tuple) -> None:
        kind = item[0]
        if kind == "cmd":
            _, name, kwargs, fut = item
            if not fut.set_running_or_notify_cancel():
                return
            fn = getattr(self, "_cmd_" + name, None)
            try:
                if fn is None:
                    raise CommandError("unknown_command", None, {"name": name})
                fut.set_result(fn(**kwargs))
            except CommandError as e:
                fut.set_exception(e)
            except TypeError as e:
                fut.set_exception(CommandError("bad_arguments", None, {"message": str(e)}))
            except Exception as e:  # noqa: BLE001
                fut.set_exception(e)
        elif kind == "sweep":
            self._on_sweep(item[1], item[2])
        elif kind == "discarded":
            self._on_discarded(item[1], item[2], item[3])
        elif kind == "disconnected":
            self._on_disconnected(item[1])
        elif kind == "invalid":
            self._notice("invalid_sweep", {"message": item[2]})
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
        }

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
        wanted = self._run in ("continuous", "single") and self._conn == "connected"
        busy = self._worker.busy_since
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
        self._sweep_count += 1
        self._last_sweep_s = raw.duration_s
        levels = self._converter.to_dbm(raw.adopted, raw.raw)
        a = raw.adopted
        self._emit(
            "trace",
            {
                "trace": 1,
                "sweep_no": self._sweep_count,
                "time": raw.started_at,
                "duration_s": raw.duration_s,
                "axis": {"start_hz": a.axis.start_hz, "step_hz": a.axis.step_hz, "points": a.axis.points},
                "display": {"start_hz": a.start_hz, "stop_hz": a.stop_hz, "tail_gap": a.tail_gap},
                "quantity": "power",
                "unit": "dBm",
                "values": levels.dbm.astype(np.float32),
                "flags": {
                    "transient_head_points": raw.transient_head_points,
                    "out_of_cal_range": levels.out_of_cal_range,
                    "no_calibration": levels.no_calibration,
                },
            },
        )
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
        self._worker = _DeviceWorker(device, self._post)
        self._worker.start()
        self._load_calibration(ident.serial)
        self._conn = "connected"
        self._push_job()
        self._changed()

    def _drop_device(self) -> None:
        if self._worker is not None:
            self._worker.close()
            self._worker = None
        self._device = None

    def _on_disconnected(self, message: str) -> None:
        self._drop_device()
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
        if not any(p.get("serial") == serial for p in ports):
            return
        try:
            device = backend.open(serial, None)
        except (DeviceDisconnected, OSError):
            return
        self._notice("reconnected", {"serial": serial})
        # 直前の条件で掃引を再開する（T8: 待ち時間なしで正常に戻る）
        self._attach(device, self._backend_name)

    def _load_calibration(self, serial: str) -> None:
        try:
            self._cal = self._cal_store.load_current(serial)
        except Exception as e:  # noqa: BLE001 — 読めない校正は「無し」として知らせる
            self._cal = None
            self._notice("calibration_unreadable", {"message": str(e)})
        self._converter = LevelConverter(self._profile, self._cal)
        if self._cal is None:
            self._notice("no_calibration", {"serial": serial})

    # ------------------------------------------------------------------ 命令

    def _cmd__shutdown(self) -> None:
        self._drop_device()
        self._settings.set("state", self._state_to_save())
        try:
            self._settings.save()
        except OSError as e:
            self._notice("settings_unwritable", {"message": str(e)})
        self._running = False

    def _cmd_get_state(self) -> dict:
        return self._snapshot()

    def _cmd_list_devices(self, backend: str = "gigast") -> dict:
        b = self._backends.get(backend)
        if b is None:
            raise CommandError("unknown_backend", "backend", {"backend": backend})
        known = self._settings.known_serials()
        ports = b.discover()
        for p in ports:
            p["known"] = p.get("serial") in known
        return {"devices": ports}

    def _cmd_connect(self, backend: str = "gigast", serial: Optional[str] = None, port: Optional[str] = None) -> dict:
        """繋ぐ。初めての機器は確認の掃引で GigaSt と確かめてから覚える（設計B §7.3）。"""
        b = self._backends.get(backend)
        if b is None:
            raise CommandError("unknown_backend", "backend", {"backend": backend})
        if self._device is not None or self._conn == "reconnecting":
            self._drop_device()
            self._conn = "disconnected"
        known = self._settings.known_serials()
        if port is None and serial is None:
            raise CommandError("device_not_selected", "serial")
        if port is None:
            ports = [p for p in b.discover() if p.get("serial") == serial]
            if not ports:
                raise CommandError("device_not_found", "serial", {"serial": serial})
            port = ports[0]["port"]
            if serial not in known and not b.probe(port):
                raise CommandError("probe_failed", "port", {"port": port})
        else:
            match = [p for p in b.discover() if p.get("port") == port]
            serial = match[0].get("serial") if match else serial
            if serial not in known and not b.probe(port):
                raise CommandError("probe_failed", "port", {"port": port})
        try:
            device = b.open(serial, port)
        except (DeviceDisconnected, OSError) as e:
            raise CommandError("open_failed", "port", {"port": port, "message": str(e)}) from e
        ident = device.identity()
        if ident.serial:
            self._settings.remember_device(ident.serial)
        self._attach(device, backend)
        return {"identity": self._identity}

    def _cmd_connect_last(self, backend: str = "gigast") -> dict:
        """覚えた機器が見つかれば自動で繋ぐ（設計B §7.3: 2回目から）。見つからなければ何もしない。"""
        b = self._backends.get(backend)
        if b is None:
            raise CommandError("unknown_backend", "backend", {"backend": backend})
        ports = b.discover()
        last = self._settings.last_serial()
        order = ([last] if last else []) + self._settings.known_serials()
        for s in order:
            if any(p.get("serial") == s for p in ports):
                return self._cmd_connect(backend=backend, serial=s)
        return {"identity": None}

    def _cmd_disconnect(self) -> dict:
        self._drop_device()
        self._identity = None
        self._backend_name = None
        self._run = "stopped"
        self._set_conn("disconnected")
        return {}

    def _apply(self, request: SweepRequest) -> dict:
        new = self._model.apply(request, self._adopted)
        # 機器へ送る中身が変わったときだけ掃引をやり直す（固定の切り替え・知らせだけの違いではやり直さない）
        changed = _sweep_key(new) != _sweep_key(self._adopted)
        self._adopted = new
        self._last_estimate = new.estimated_sweep_s
        if changed:
            if self._run == "single":
                self._single_done = 0
            self._push_job()
        self._changed()
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
        image_cancel: Optional[bool] = None,
        span_lock: Optional[bool] = None,
        one_shot: bool = False,
    ) -> dict:
        for name, v in (("start_hz", start_hz), ("stop_hz", stop_hz), ("center_hz", center_hz),
                        ("span_hz", span_hz), ("rbw_hz", rbw_hz), ("points", points)):
            if v is not None and (isinstance(v, bool) or not isinstance(v, int)):
                raise CommandError("not_integer", name)
        if edited is None:
            edited = [n for n, v in (("start_hz", start_hz), ("stop_hz", stop_hz), ("center_hz", center_hz),
                                     ("span_hz", span_hz)) if v is not None]
        req = SweepRequest(
            start_hz=start_hz, stop_hz=stop_hz, center_hz=center_hz, span_hz=span_hz,
            edited=tuple(edited), rbw_preference_hz=rbw_hz,
            points=points, image_cancel=image_cancel,
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
        if mode == "continuous":
            self._run = "continuous"
        elif mode == "single":
            # 回数を持つトレースは④で作る。いまは無いので1回（設計F §5.2.1）
            self._run = "single"
            self._single_target = max(1, int(count or 1))
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
            self._push_job()  # 校正が変わると値の意味が変わる（設計F §5.3）
        self._changed()
        return {"summary": cal.to_dict()}
