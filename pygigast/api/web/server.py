"""Web の口（設計A §4.3）: `POST /api/v1/{命令名}`・`GET /api/v1/state`・`WS /api/v1/events`。

- 命令は同じプロセスの口（api/local.py）へそのまま渡す。名前・引数・戻り値は Qt と同じ
- 失敗は A §4.3 の失敗の形 `{"error": {"code", "field", "detail"}}`。文言は画面側（U-22）
- イベントは制御スレッドから届くので、asyncio のループへ call_soon_threadsafe で渡し直す（A §4.2）
- トレースはバイナリ（先頭4バイトが見出しの長さ、JSON の見出し、float32 の値）。
  番号ごとに 30回/秒 までに間引き、途中のものは捨てる（A §4.3: 画面の更新だけ。保存・記録はコアが行うので間引かない）
- 状態とマーカー表は全体を毎回送るので、遅い画面には最新の1つだけを送る（途中の版は要らない）
- 聞く機能の音（イベント audio）もバイナリ（見出し JSON の kind が "audio"、float32 の標本）。遅い画面には
  新しい方を残して古い音を捨てる（遅れをためない）
- 画面の文言は view.py が足す（状態の ui・知らせの view・失敗の text・戻り値の texts。Qt と同じ関数で作る: J §5）
"""
from __future__ import annotations

import asyncio
import collections
import concurrent.futures
import dataclasses
import datetime
import enum
import ipaddress
import json
import math
import struct
import threading
from pathlib import Path
from typing import Any, Optional

import numpy as np
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ... import __version__
from ..local import CommandError, LocalApi
from ...core import report_layout as rl
from . import view

UI_WEB_DIR = Path(__file__).resolve().parents[2] / "ui_web"

# トレースを1本あたり送る上限（回/秒）。画面の更新周期（A §4.3 の例 30回/秒）
TRACE_RATE_HZ = 30.0
# 遅い画面に溜める知らせ・進み具合の上限。超えたら古いものから捨てる（状態は最新の全体が別に届く）
MAX_PENDING_NOTICES = 500
# 遅い画面に溜める音の塊の上限（1塊は数 ms〜十数 ms。超えたら古い音から捨てる）
MAX_PENDING_AUDIO = 64

# 失敗のコードと HTTP の状態（設計に無い判断: 命令が無いは 404、それ以外の命令の失敗は 400）
_STATUS_OF = {"unknown_command": 404}


# ---------------------------------------------------------------------------
# JSON に写す
# ---------------------------------------------------------------------------


def to_jsonable(v: Any) -> Any:
    """コアの値を JSON に写せる形にする。NaN・無限大は null（JSON に無いので）。"""
    if v is None or isinstance(v, (bool, str)):
        return v
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, enum.Enum):
        return to_jsonable(v.value)
    if isinstance(v, dict):
        return {str(k): to_jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set, frozenset)):
        return [to_jsonable(x) for x in v]
    if isinstance(v, np.ndarray):
        if v.dtype.kind == "f":
            return [None if not math.isfinite(x) else x for x in v.astype(np.float64).tolist()]
        return v.tolist()
    if isinstance(v, np.generic):
        return to_jsonable(v.item())
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.isoformat()
    if isinstance(v, Path):
        return str(v)
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return to_jsonable(dataclasses.asdict(v))
    if isinstance(v, bytes):
        return None
    return str(v)


def dumps(v: Any) -> str:
    return json.dumps(to_jsonable(v), ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def error_body(e: BaseException) -> dict:
    if isinstance(e, CommandError):
        return e.to_dict()
    return {"error": {"code": "internal_error", "field": None, "detail": {"message": repr(e)}}}


def _ranges(a: Any) -> list[list[int]]:
    """真偽の配列を [始まり, 終わり) の組の並びにする（12000 点の印を小さく送る）。"""
    b = np.asarray(a, dtype=bool)
    if b.size == 0 or not b.any():
        return []
    d = np.diff(np.concatenate(([0], b.view(np.int8), [0])).astype(np.int8))
    starts = np.flatnonzero(d == 1)
    ends = np.flatnonzero(d == -1)
    return [[int(s), int(e)] for s, e in zip(starts, ends)]


def encode_trace(payload: dict) -> bytes:
    """イベント `trace` をバイナリにする（A §4.3）。

    形: [見出しの長さ uint32 LE][見出し JSON（UTF-8。値の並びが4バイト境界に来るよう空白で埋める）][値 float32 LE]
    点ごとの印は [始まり, 終わり) の組の並びにして見出しに入れる。不等間隔の周波数軸は見出しに Hz の整数で入れる。
    """
    values = np.asarray(payload.get("values", ()), dtype="<f4")
    header = {k: v for k, v in payload.items() if k not in ("values", "flags")}
    flags_out: dict = {}
    for k, v in (payload.get("flags") or {}).items():
        if k == "points":
            flags_out["points"] = {name: _ranges(arr) for name, arr in (v or {}).items()}
        elif isinstance(v, np.ndarray):
            flags_out[k] = _ranges(v)
        else:
            flags_out[k] = v
    header["flags"] = flags_out
    header["kind"] = "trace"
    header["value_type"] = "float32"
    header["value_count"] = int(values.size)
    hb = dumps(header).encode("utf-8")
    hb += b" " * ((-len(hb)) % 4)
    return struct.pack("<I", len(hb)) + hb + values.tobytes()


def encode_audio(payload: dict) -> bytes:
    """イベント `audio`（設計Q §6.8）をバイナリにする。形はトレースと同じ（見出しの kind が "audio"）。"""
    values = np.asarray(payload.get("samples", ()), dtype="<f4")
    header = {k: v for k, v in payload.items() if k != "samples"}
    header["kind"] = "audio"
    header["value_type"] = "float32"
    header["value_count"] = int(values.size)
    hb = dumps(header).encode("utf-8")
    hb += b" " * ((-len(hb)) % 4)
    return struct.pack("<I", len(hb)) + hb + values.tobytes()


def texts_json() -> dict:
    """画面の文言（ui_qt/texts.py の表）。Qt と Web で1つの正本を使う（J §5: 同じ語）。

    texts.py は Qt を import しない（コアの units だけ）ので、画面なしの serve でも読める。
    """
    from ...ui_qt import texts

    out = {}
    for name in dir(texts):
        if not name.isupper():
            continue
        v = getattr(texts, name)
        if isinstance(v, (dict, str)):
            out[name] = v
        elif isinstance(v, (set, frozenset, tuple, list)):
            out[name] = sorted(v) if isinstance(v, (set, frozenset)) else list(v)
    return to_jsonable(out)


# ---------------------------------------------------------------------------
# イベントを画面へ配る
# ---------------------------------------------------------------------------


class _Client:
    """繋いでいる画面1つ。遅い画面には、状態・マーカー表・トレースは最新の1つだけを送る。"""

    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self._latest: dict[str, tuple[int, str]] = {}  # state / markers → (版, JSON)
        self._notices: collections.deque[str] = collections.deque(maxlen=MAX_PENDING_NOTICES)
        self._traces: dict[str, bytes] = {}
        self._audio: collections.deque[bytes] = collections.deque(maxlen=MAX_PENDING_AUDIO)
        self._wake = asyncio.Event()

    def put_latest(self, kind: str, version: int, text: str) -> None:
        old = self._latest.get(kind)
        if old is not None and old[0] > version:
            return  # 古い版で新しい版を上書きしない（繋いだ直後の get_state とイベントが前後したとき）
        self._latest[kind] = (version, text)
        self._wake.set()

    def put_notice(self, text: str) -> None:
        self._notices.append(text)
        self._wake.set()

    def put_trace(self, key: str, data: bytes) -> None:
        self._traces[key] = data
        self._wake.set()

    def put_audio(self, data: bytes) -> None:
        self._audio.append(data)
        self._wake.set()

    async def run(self) -> None:
        try:
            await self._run()
        except (WebSocketDisconnect, RuntimeError, OSError):
            pass  # 画面が閉じた。後始末は受け口の側が行う
        except Exception:  # noqa: BLE001 — 送れない形の値でも、他の画面への配信は止めない
            import traceback

            traceback.print_exc()

    async def _run(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            for kind in ("state", "markers"):
                item = self._latest.pop(kind, None)
                if item is not None:
                    await self.ws.send_text(item[1])
            while self._notices:
                await self.ws.send_text(self._notices.popleft())
            while self._audio:
                await self.ws.send_bytes(self._audio.popleft())
            while self._traces:
                key = next(iter(self._traces))
                await self.ws.send_bytes(self._traces.pop(key))


class _Hub:
    """制御スレッドのイベントを受け、asyncio のループで全部の画面へ配る。"""

    def __init__(self, api: LocalApi, trace_rate_hz: float) -> None:
        self.api = api
        self.clients: set[_Client] = set()
        self._interval = 1.0 / trace_rate_hz if trace_rate_hz > 0 else 0.0
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._unsub = None
        self._last_sent: dict[str, float] = {}
        self._pending: dict[str, dict] = {}
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._markers_seq = 0
        self.last_state: Optional[dict] = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._unsub = self.api.subscribe(self._from_control_thread)

    def stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        for h in self._timers.values():
            h.cancel()
        self._timers.clear()

    def _from_control_thread(self, kind: str, payload: dict) -> None:
        # 制御スレッドで呼ばれる。ここでは何も触らずにループへ渡す（A §4.2）
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self.dispatch, kind, payload)
        except RuntimeError:
            pass  # ループが閉じた後（終了の途中）

    def dispatch(self, kind: str, payload: dict) -> None:
        if kind == "trace":
            self._trace(payload)
            return
        if kind == "audio":
            data = encode_audio(payload)
            for c in self.clients:
                c.put_audio(data)
            return
        if kind == "state":
            self.last_state = payload
            payload = view.state_view(payload)
        elif kind == "markers":
            payload = view.markers_view(payload, view.default_unit(self.last_state))
        elif kind == "notice":
            payload = view.notice_view(payload, self.last_state)
        elif kind == "progress":
            payload = view.progress_view(payload)
        elif kind == "devices":
            payload = view.devices_view(payload)
        text = dumps({"kind": kind, "payload": payload})
        if kind == "state":
            ver = int(payload.get("version", 0))
            for c in self.clients:
                c.put_latest("state", ver, text)
        elif kind == "markers":
            # マーカー表は版を持たないので、届いた順の番号で最新を見分ける
            self._markers_seq += 1
            for c in self.clients:
                c.put_latest("markers", self._markers_seq, text)
        else:
            for c in self.clients:
                c.put_notice(text)

    def _trace(self, payload: dict) -> None:
        key = str(payload.get("trace"))
        loop = self._loop
        now = loop.time()
        due = self._last_sent.get(key, -math.inf) + self._interval
        if now >= due and key not in self._timers:
            self._send_trace(key, payload)
            return
        # 間隔の内に来たものは最後の1本だけを残し、間隔が明けたら送る（途中のものは捨てる）
        self._pending[key] = payload
        if key not in self._timers:
            self._timers[key] = loop.call_at(due, self._flush, key)

    def _flush(self, key: str) -> None:
        self._timers.pop(key, None)
        payload = self._pending.pop(key, None)
        if payload is not None:
            self._send_trace(key, payload)

    def _send_trace(self, key: str, payload: dict) -> None:
        self._last_sent[key] = self._loop.time()
        data = encode_trace(payload)
        for c in self.clients:
            c.put_trace(key, data)


# ---------------------------------------------------------------------------
# アプリ
# ---------------------------------------------------------------------------


async def _await(fut: concurrent.futures.Future) -> Any:
    return await asyncio.wrap_future(fut)


def create_app(api: LocalApi, *, trace_rate_hz: float = TRACE_RATE_HZ, lan: bool = False,
               static_dir: Path = UI_WEB_DIR) -> FastAPI:
    """LocalApi に繋いだ FastAPI のアプリを作る。lan は画面に知らせるだけ（PNG のコピーの可否の説明など）。"""
    hub = _Hub(api, trace_rate_hz)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        hub.start(asyncio.get_running_loop())
        try:
            yield
        finally:
            hub.stop()

    app = FastAPI(title="pyGigaSt", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.hub = hub

    @app.get("/api/v1/state")
    async def get_state() -> JSONResponse:
        try:
            st = await _await(api.get_state())
        except Exception as e:  # noqa: BLE001
            return JSONResponse(error_body(e), status_code=500)
        return JSONResponse(to_jsonable(view.state_view(st)))

    @app.get("/api/v1/texts")
    async def get_texts() -> JSONResponse:
        return JSONResponse(texts_json())

    @app.get("/api/v1/info")
    async def get_info() -> JSONResponse:
        # 保存先の欄の初期値（サーバーの PC の上のパス。設計に無い判断: 利用者のホーム）
        # 配色の初期値（J §4.4。Qt と同じ値を1か所から: U-09）も渡す
        return JSONResponse({"app": "pyGigaSt", "version": __version__, "lan": lan,
                             "default_dir": str(Path.home()), "trace_rate_hz": trace_rate_hz,
                             "palettes": {"dark": rl.DARK_DEFAULT, "light": rl.LIGHT_DEFAULT}})

    @app.post("/api/v1/png_layout")
    async def png_layout(request: Request) -> JSONResponse:
        """Web の PNG の配置（I §3.1: 配置はコアに1つ。Qt と同じ report_layout の結果）。"""
        try:
            req = json.loads(await request.body() or b"{}")
            if not isinstance(req, dict):
                raise ValueError("not an object")
            st = await _await(api.get_state())
            return JSONResponse(to_jsonable(view.png_layout(st, req)))
        except ValueError as e:
            return JSONResponse(view.error_view({"error": {"code": "bad_value", "field": None,
                                                           "detail": {"message": str(e)}}}), status_code=400)

    @app.post("/api/v1/{command}")
    async def post_command(command: str, request: Request) -> JSONResponse:
        raw = await request.body()
        try:
            args = json.loads(raw) if raw.strip() else {}
        except ValueError:
            return JSONResponse({"error": {"code": "bad_json", "field": None, "detail": {}}}, status_code=400)
        if not isinstance(args, dict):
            return JSONResponse({"error": {"code": "bad_json", "field": None, "detail": {}}}, status_code=400)
        try:
            # 命令の名前は位置で渡す（引数に name を持つ命令とぶつけないため）
            fut = api.call(command, **args)
            result = await _await(fut)
        except CommandError as e:
            return JSONResponse(to_jsonable(view.error_view(e.to_dict())), status_code=_STATUS_OF.get(e.code, 400))
        except Exception as e:  # noqa: BLE001
            return JSONResponse(error_body(e), status_code=500)
        return JSONResponse(to_jsonable(view.result_view(command, args, {} if result is None else result)))

    @app.websocket("/api/v1/events")
    async def events(ws: WebSocket) -> None:
        await ws.accept()
        client = _Client(ws)
        # 先に配る先に入れてから今の状態を取る（取る間に起きた変化を落とさない。古い版は _Client が捨てる）
        hub.clients.add(client)
        sender = asyncio.create_task(client.run())
        try:
            st = await _await(api.get_state())
            client.put_latest("state", int(st.get("version", 0)),
                              dumps({"kind": "state", "payload": view.state_view(st)}))
            # 途中から繋いだ画面にも、いま出ているトレースを描かせる
            ids: list = [t["number"] for t in st.get("traces", []) if t.get("has_values") and t.get("visible")]
            ids += [tr["id"] for f in st.get("files", []) for tr in f.get("traces", [])]
            for tid in ids:
                try:
                    p = await _await(api.call("get_trace", id=tid))
                except Exception:  # noqa: BLE001 — 取る間に消えたトレースは飛ばす
                    continue
                client.put_trace(str(tid), encode_trace(p))
            while True:
                # 受け取るものは無い（命令は POST）。切れたことを知るために待つ
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            hub.clients.discard(client)
            sender.cancel()

    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="ui_web")
    return app


# ---------------------------------------------------------------------------
# 起動
# ---------------------------------------------------------------------------


def check_host(host: str, lan: bool) -> None:
    """既定は自分の PC だけ。LAN へ開くのは --lan を付けたときだけ（Web の口に認証が無いため: A §4.3 の未決）。"""
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if not loopback and not lan:
        raise ValueError(f"{host} は自分の PC の外から繋がります。LAN へ開くときは --lan を付けてください")


def _config(api: LocalApi, host: str, port: int, lan: bool):
    import uvicorn

    check_host(host, lan)
    app = create_app(api, lan=lan)
    return uvicorn.Config(app, host=host, port=port, log_level="warning", lifespan="on", ws="auto")


def serve(api: LocalApi, host: str = "127.0.0.1", port: int = 8080, lan: bool = False) -> None:
    """画面なしで Web の口だけを開き、止められるまで待つ（`pygigast serve`）。"""
    import uvicorn

    uvicorn.Server(_config(api, host, port, lan)).run()


def run_in_thread(api: LocalApi, host: str = "127.0.0.1", port: int = 8080, lan: bool = False):
    """Qt と同時に Web の口を開く（`pygigast --web`）。止めるときは戻り値の server.should_exit = True。"""
    import uvicorn

    server = uvicorn.Server(_config(api, host, port, lan))
    th = threading.Thread(target=server.run, name="pygigast-web", daemon=True)
    th.start()
    return server, th
