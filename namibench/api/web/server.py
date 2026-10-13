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
- 入口の確かめ（Host・Origin・送る形・認証・見るだけ）は guard.py、パスを受け取らない形は webfiles.py、
  鍵・認証番号・接続の一覧は access.py、認証・ダウンロード・アップロードの受け口は routes_access.py
  （設計A §4.3.2）。開く・閉じる・LAN の切り替えは service.py
"""
from __future__ import annotations

import asyncio
import collections
import concurrent.futures
import dataclasses
import datetime
import enum
import json
import math
import struct
from pathlib import Path
from typing import Any, Optional

import numpy as np
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ... import __version__
from ..local import CommandError, LocalApi
from ...core import colormaps
from ...core import report_layout as rl
from ...ui_qt import screen_model, source_view, texts
from . import view
from .access import KIND_OPERATE, Access
from .guard import Guard
from .webfiles import WebArgError, WebFiles, translate

UI_WEB_DIR = Path(__file__).resolve().parents[2] / "ui_web"

# トレースを1本あたり送る上限（回/秒）。画面の更新周期（A §4.3 の例 30回/秒）
TRACE_RATE_HZ = 30.0
# 遅い画面に溜める知らせ・進み具合の上限。超えたら古いものから捨てる（状態は最新の全体が別に届く）
MAX_PENDING_NOTICES = 500
# 遅い画面に溜める音の塊の上限（1塊は数 ms〜十数 ms。超えたら古い音から捨てる）
MAX_PENDING_AUDIO = 64
# 遅い画面に溜めるウォーターフォールの行の上限（超えたら古い行から捨てる。画面は番号の飛びを見て取り直す）
MAX_PENDING_WATERFALL = 512

# 失敗のコードと HTTP の状態（設計に無い判断: 命令が無いは 404、それ以外の命令の失敗は 400）
STATUS_OF = {"unknown_command": 404, "unknown_file": 404, "file_not_ready": 409, "upload_too_large": 413,
             "wrong_code": 401, "bad_key": 401, "blocked": 429, "read_only": 403, "lan_off": 409}
_TASK_END = ("task_done", "task_failed", "task_cancelled")


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
        if v.ndim > 1:
            return [to_jsonable(r) for r in v]  # [段, 点] など（ステップ掃引の段ごとの線）
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
    header = {k: v for k, v in payload.items() if k not in ("values", "flags", "steps")}
    steps = payload.get("steps")
    tail = b""
    if steps is not None:
        # ステップ掃引の段ごとの線（設計L §1.3）: 値の後に [段, 点] の float32 を続け、見出しに形と横の位置を入れる
        rows = np.asarray(steps["values"], dtype="<f4")
        header["steps"] = {"x0": steps["x0"], "dx": steps["dx"], "count": int(rows.shape[0]),
                           "points": int(rows.shape[1]) if rows.ndim == 2 else 0}
        tail = rows.tobytes()
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
    return struct.pack("<I", len(hb)) + hb + values.tobytes() + tail


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


def encode_waterfall(payload: dict) -> bytes:
    """イベント `waterfall`・命令 `waterfall_rows` の戻り値をバイナリにする（設計P §8: 掃引データはバイナリのまま）。

    形はトレースと同じ: [見出しの長さ uint32 LE][見出し JSON][値 float32 LE]。見出しの rows に行ごとの見出し（n は列の数）、
    値は行を古い順につないだもの。
    """
    rows = payload.get("rows") or []
    values = (np.concatenate([np.asarray(r["values"], dtype="<f4") for r in rows]) if rows
              else np.zeros(0, dtype="<f4"))
    header = {k: v for k, v in payload.items() if k != "rows"}
    header["rows"] = [{k: v for k, v in r.items() if k != "values"} for r in rows]
    header["kind"] = "waterfall"
    header["value_type"] = "float32"
    header["value_count"] = int(values.size)
    hb = dumps(header).encode("utf-8")
    hb += b" " * ((-len(hb)) % 4)
    return struct.pack("<I", len(hb)) + hb + values.astype("<f4").tobytes()


def encode_density(payload: dict) -> bytes:
    """イベント `density` をバイナリにする。値は [縦, 横] の uint8（0 は来ていない）を行の順につないだもの。

    何秒分の密度かの文（seconds_text）は、この絵と一緒に届いた秒から作る（Qt の density_view と同じ関数）。状態の秒から
    作ると、再生中に密度の元を記録から読み終えた後（状態は出ない）に前の秒のまま残る（設計P §9.3）。"""
    from ...ui_qt.display_view import density_seconds_text

    img = np.asarray(payload.get("image") if payload.get("image") is not None else np.zeros((0, 0)), dtype=np.uint8)
    header = {k: v for k, v in payload.items() if k != "image"}
    header["seconds_text"] = density_seconds_text(payload.get("seconds")) if img.size else ""
    header["kind"] = "density"
    header["value_type"] = "uint8"
    header["value_count"] = int(img.size)
    hb = dumps(header).encode("utf-8")
    hb += b" " * ((-len(hb)) % 4)
    return struct.pack("<I", len(hb)) + hb + np.ascontiguousarray(img).tobytes()


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
        # ウォーターフォールの行は全部を順に送る（間引くと行が欠ける。溢れたら画面が番号の飛びを見て取り直す）
        self._waterfall: collections.deque[bytes] = collections.deque(maxlen=MAX_PENDING_WATERFALL)
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

    def put_waterfall(self, data: bytes) -> None:
        self._waterfall.append(data)
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
            for kind in ("state", "markers", "sweep_progress"):
                item = self._latest.pop(kind, None)
                if item is not None:
                    await self.ws.send_text(item[1])
            while self._notices:
                await self.ws.send_text(self._notices.popleft())
            while self._audio:
                await self.ws.send_bytes(self._audio.popleft())
            while self._waterfall:
                await self.ws.send_bytes(self._waterfall.popleft())
            while self._traces:
                key = next(iter(self._traces))
                await self.ws.send_bytes(self._traces.pop(key))


class _Hub:
    """制御スレッドのイベントを受け、asyncio のループで全部の画面へ配る。"""

    def __init__(self, api: LocalApi, trace_rate_hz: float, files: Optional[WebFiles] = None) -> None:
        self.api = api
        self.files = files
        self.clients: set[_Client] = set()
        self._interval = 1.0 / trace_rate_hz if trace_rate_hz > 0 else 0.0
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._unsub = None
        self._last_sent: dict[str, float] = {}
        self._pending: dict[str, dict] = {}
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._markers_seq = 0
        # 掃引の進み具合（設計J §4.13）。最新の1つだけを送り、途中から繋いだ画面にも送る
        self._progress_seq = 0
        self.last_progress: Optional[str] = None
        self.last_state: Optional[dict] = None
        # 測定中のトレース 1〜4 の最新の中身（間引く前）。マウスの位置の読み値（設計G §2）に使う
        self.latest_traces: dict[str, dict] = {}

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
        if kind == "waterfall":
            data = encode_waterfall(payload)
            for c in self.clients:
                c.put_waterfall(data)
            return
        if kind == "density":
            # 密度の絵は最新の1枚だけでよい（トレースと同じ間引き）
            self._trace({**payload, "trace": "density", "source": "density"})
            return
        if kind == "state":
            self.last_state = payload
            # 隠した・値を捨てたトレースの中身は忘れる（隠している間の値はイベントで届かないので、出し直したら取り直す）
            shown = {str(t.get("number")) for t in payload.get("traces") or [] if t.get("visible") and t.get("has_values")}
            for key in [k for k in self.latest_traces if k not in shown]:
                del self.latest_traces[key]
            payload = view.state_view(payload)
        elif kind == "markers":
            payload = view.markers_view(payload, view.default_unit(self.last_state),
                                        texts.freq_offset_of(self.last_state))
        elif kind == "notice":
            if self.files is not None and payload.get("code") in _TASK_END:
                # Web の口で名前を付けて作らせたファイル: 終わったらダウンロードの番号を知らせに足す（A §4.3.2 の 3）
                d = payload.get("detail") or {}
                fid = self.files.task_ended(str(d.get("task_id")), payload.get("code") == "task_done")
                if fid is not None:
                    payload = {**payload, "detail": {**d, "file_id": fid}}
            payload = view.notice_view(payload, self.last_state)
        elif kind == "progress":
            payload = view.progress_view(payload)
        elif kind == "devices":
            payload = view.devices_view(payload)
        elif kind == "sweep_progress":
            payload = view.sweep_progress_view(payload)
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
        elif kind == "sweep_progress":
            self._progress_seq += 1
            self.last_progress = text
            for c in self.clients:
                c.put_latest("sweep_progress", self._progress_seq, text)
        else:
            for c in self.clients:
                c.put_notice(text)

    def _trace(self, payload: dict) -> None:
        key = str(payload.get("trace"))
        if payload.get("source") == "live":
            self.latest_traces[key] = payload
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

    async def shown_traces(self) -> list[dict]:
        """画面が表示している測定中のトレース（状態の visible かつ has_values。Qt の TraceLayer.shown_payloads と同じ）。

        まだ中身を受けていないもの（繋いだ直後・出し直した直後）は get_trace で1回だけ取る。
        """
        st = self.last_state
        if st is None:
            st = self.last_state = await _await(self.api.get_state())
        out = []
        from ...ui_qt.screen_s11 import axis_unit, trace_drawn_reason

        axis = axis_unit(st)
        for t in st.get("traces") or []:
            if not (t.get("visible") and t.get("has_values")):
                continue
            if trace_drawn_reason(t, axis):
                continue  # 縦軸と単位が違うトレースは描かないので読まない（Qt の TraceLayer.shown_payloads と同じ）
            key = str(t.get("number"))
            p = self.latest_traces.get(key)
            if p is None:
                try:
                    p = await _await(self.api.call("get_trace", id=int(t["number"])))
                except CommandError:
                    continue
                self.latest_traces.setdefault(key, p)
            out.append(p)
        return out

    def _send_trace(self, key: str, payload: dict) -> None:
        self._last_sent[key] = self._loop.time()
        data = encode_density(payload) if payload.get("source") == "density" else encode_trace(payload)
        for c in self.clients:
            c.put_trace(key, data)


# ---------------------------------------------------------------------------
# アプリ
# ---------------------------------------------------------------------------


async def _await(fut: concurrent.futures.Future) -> Any:
    return await asyncio.wrap_future(fut)


def create_app(api: LocalApi, *, trace_rate_hz: float = TRACE_RATE_HZ, lan: bool = False,
               static_dir: Path = UI_WEB_DIR, access: Optional[Access] = None,
               files: Optional[WebFiles] = None) -> Guard:
    """LocalApi に繋いだ Web の口を作る（入口の確かめ Guard で包んだ ASGI のアプリ）。

    access・files は起動の間ずっと同じものを渡す（LAN の切り替えでサーバーを開き直しても札とファイルを保つ）。
    省くとここで作る（files はアプリのデータのフォルダの web_files を空にして使う）。
    """
    if access is None:
        access = Access(lan=lan)
    if files is None:
        files = WebFiles(Path(api.data_root) / "web_files")
        files.reset()
    hub = _Hub(api, trace_rate_hz, files)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        hub.start(asyncio.get_running_loop())
        try:
            yield
        finally:
            hub.stop()

    app = FastAPI(title="pyGigaSt", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.hub = hub

    def gs(owner) -> dict:
        # Guard が確かめた接続の種類（Guard を通らない呼び方は自分の PC と同じ扱い）
        return owner.scope.get("state", {}).get("gs") or {"kind": KIND_OPERATE, "local": True, "ip": ""}

    def fail(code: str, field: Optional[str] = None, detail: Optional[dict] = None) -> JSONResponse:
        body = view.error_view({"error": {"code": code, "field": field, "detail": detail or {}}})
        return JSONResponse(to_jsonable(body), status_code=STATUS_OF.get(code, 400))

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
    async def get_info(request: Request) -> JSONResponse:
        who = gs(request)
        # 配色の初期値（J §4.4）とトレースの線の描き方（設計F §9）も渡す（Qt と同じ値を1か所から: U-09）
        return JSONResponse({"app": "pyGigaSt", "version": __version__, "lan": access.lan,
                             "trace_rate_hz": trace_rate_hz,
                             "palettes": {"dark": rl.DARK_DEFAULT, "light": rl.LIGHT_DEFAULT},
                             "line_styles": rl.LINE_STYLES,
                             # ウォーターフォールと密度表示の色の並び（256 段。Qt と同じ表: 設計P §8）
                             "colormaps": colormaps.luts_for_web(),
                             "access": {"kind": who["kind"], "local": who["local"]}})

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

    @app.post("/api/v1/mouse_reading")
    async def mouse_reading(request: Request) -> JSONResponse:
        """グラフの上のマウスの位置の読み値（設計G §2）。中身は Qt のツールチップと同じ screen_model.mouse_reading。

        本体の命令の列を通さない（マウスを動かすたびに測定の命令を待たせないため）。受けるのは
        {"x": 軸の値, "y": 縦軸の値}。読むのはこのサーバーが受けた最新のトレース。
        """
        try:
            req = json.loads(await request.body() or b"{}")
            x, y = float(req["x"]), float(req["y"])
            # グラフの1画素の周波数の幅（内部の漏れの印をマウスで拾う幅: 設計L §1.4。無ければ 0）
            px_hz = float(req.get("px_hz") or 0.0)
            if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(px_hz)):
                raise ValueError("not finite")
        except (ValueError, KeyError, TypeError) as e:
            return fail("bad_value", None, {"message": str(e)})
        payloads = await hub.shown_traces()
        zero = ((hub.last_state or {}).get("sweep") or {}).get("step_hz") == 0
        leaks = source_view.leak_marks_view(hub.last_state)
        return JSONResponse(to_jsonable(screen_model.mouse_reading(payloads, x, y, zero,
                                                                   texts.freq_offset_of(hub.last_state),
                                                                   leaks=leaks, px_hz=px_hz)))

    @app.post("/api/v1/waterfall_rows")
    async def waterfall_rows(request: Request):
        """ウォーターフォールの行（命令 waterfall_rows と同じ引数）。戻り値はイベントと同じバイナリ（設計P §8）。"""
        from fastapi.responses import Response

        try:
            args = json.loads(await request.body() or b"{}")
            if not isinstance(args, dict):
                raise ValueError("not an object")
        except ValueError:
            return fail("bad_json")
        try:
            r = await _await(api.call("waterfall_rows", **args))
        except CommandError as e:
            return JSONResponse(to_jsonable(view.error_view(e.to_dict())), status_code=STATUS_OF.get(e.code, 400))
        return Response(content=encode_waterfall(r), media_type="application/octet-stream")

    @app.post("/api/v1/waterfall_reading")
    async def waterfall_reading(request: Request) -> JSONResponse:
        """ウォーターフォールの行の上のマウスの読み値の文（Qt のツールチップと同じ display_view.waterfall_reading）。

        受けるのは {"t", "sweeps", "freq_hz" か "point", "level", "unit"}（値は画面が持つ行から）。文を作るだけで、
        本体の命令の列を通さない。
        """
        try:
            req = json.loads(await request.body() or b"{}")
            t = float(req["t"])
            level = req.get("level")
            level = None if level is None else float(level)
            freq = req.get("freq_hz")
            freq = None if freq is None else int(freq)
            point = req.get("point")
            point = None if point is None else int(point)
        except (ValueError, KeyError, TypeError) as e:
            return fail("bad_value", None, {"message": str(e)})
        from ...ui_qt import display_view

        lines = display_view.waterfall_reading(t, int(req.get("sweeps") or 1), freq, level, str(req.get("unit") or ""),
                                               texts.freq_offset_of(hub.last_state), point)
        return JSONResponse({"lines": lines})

    from .routes_access import add_access_routes

    add_access_routes(app, access, files, gs, fail)

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
            # Web の口はパスを受け取らない。名前・アップロードの番号・記録の番号をコアの形へ写す（A §4.3.2 の 3）
            tr = translate(files, command, args)
        except WebArgError as e:
            return fail(e.code, e.field, e.detail)
        try:
            # 命令の名前は位置で渡す（引数に name を持つ命令とぶつけないため）
            fut = api.call(command, **tr.args)
            # 作らせるファイルと処理の番号は、制御スレッドが結果を入れたその場で結び付ける（終わりの知らせより先: webfiles.Translated）
            fut.add_done_callback(lambda f: tr.bind(f.result()) if not f.cancelled() and f.exception() is None else None)
            result = tr.after(await _await(fut))
        except CommandError as e:
            tr.on_error()
            return JSONResponse(to_jsonable(view.error_view(e.to_dict())), status_code=STATUS_OF.get(e.code, 400))
        except Exception as e:  # noqa: BLE001
            tr.on_error()
            return JSONResponse(error_body(e), status_code=500)
        # 調整の知らせの周波数は欄と同じく換算後（周波数オフセット: 設計O）
        return JSONResponse(to_jsonable(view.result_view(command, args, {} if result is None else result,
                                                         texts.freq_offset_of(hub.last_state))))

    @app.websocket("/api/v1/events")
    async def events(ws: WebSocket) -> None:
        await ws.accept()
        client = _Client(ws)
        who = gs(ws)
        loop = asyncio.get_running_loop()

        def close_ws() -> None:
            # 一覧の［切断］・認証を作り直す・LAN を OFF（ほかのスレッドから呼ばれる）
            async def _close() -> None:
                try:
                    await ws.close(code=4401)
                except Exception:  # noqa: BLE001 — もう閉じている
                    pass

            try:
                loop.call_soon_threadsafe(lambda: asyncio.ensure_future(_close()))
            except RuntimeError:
                pass

        cid = access.add_conn(who["kind"], who["ip"], who.get("session"), who["local"], close_ws)
        # 先に配る先に入れてから今の状態を取る（取る間に起きた変化を落とさない。古い版は _Client が捨てる）
        hub.clients.add(client)
        sender = asyncio.create_task(client.run())
        try:
            st = await _await(api.get_state())
            client.put_latest("state", int(st.get("version", 0)),
                              dumps({"kind": "state", "payload": view.state_view(st)}))
            if hub.last_progress is not None:
                client.put_latest("sweep_progress", 0, hub.last_progress)  # 届いていれば後の知らせが上書きする
            # 途中から繋いだ画面にも、いま出ているトレースを描かせる
            ids: list = [t["number"] for t in st.get("traces", []) if t.get("has_values") and t.get("visible")]
            ids += [tr["id"] for f in st.get("files", []) for tr in f.get("traces", [])]
            for tid in ids:
                try:
                    p = await _await(api.call("get_trace", id=tid))
                except Exception:  # noqa: BLE001 — 取る間に消えたトレースは飛ばす
                    continue
                client.put_trace(str(tid), encode_trace(p))
            # 密度表示のいまの絵（途中から繋いだ画面にも: 設計P §4）
            try:
                d = await _await(api.call("density_get"))
                if d.get("image") is not None:
                    client.put_trace("density", encode_density(d))
            except Exception:  # noqa: BLE001
                pass
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
            access.remove_conn(cid)

    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="ui_web")
    guard = Guard(app, access)
    guard.hub = hub
    guard.fastapi = app
    guard.files = files
    return guard
