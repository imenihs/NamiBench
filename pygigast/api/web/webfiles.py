"""Web の口はサーバーの PC のパスを受け取らない（設計A §4.3.2 の 3）。その代わりの形をここで作る。

- 保存・CSV の書き出し・記録のまとめ・まとめ終わっていない記録のまとめ: 名前だけを受け、アプリのデータの
  フォルダの `web_files\\` の中に作ってコアの命令（パスを取る形）へ渡す。終わったら `file_id` で
  `GET /api/v1/files/{file_id}` からダウンロードできる
- 開く・旧校正ファイル: ブラウザが `PUT /api/v1/uploads?name=…` で送ったファイル（`upload_id`）を使う
- まとめ終わっていない記録: 一覧の行に番号（`id`）を付け、番号で選ぶ（パスは出さない）
- `web_files\\` は起動のたびに空にする（1回の起動の間だけ置く）
- `path`・`paths`・`h5_path`・`dll_folder` を含む命令は断る（`path_not_allowed`）

コアの命令の名前・引数はそのまま（Qt はパスで使う）。ここは Web の口の形とコアの形の間の写しだけを持つ。
"""
from __future__ import annotations

import datetime
import re
import secrets
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

PATH_ARGS = ("path", "paths", "h5_path", "dll_folder")
MAX_UPLOAD_BYTES = 2 * 1024 ** 3  # 2GB（記録のファイルを開くため）
MAX_NAME_CHARS = 120

# 名前に使えない文字（Windows のファイル名で使えないものと制御文字）
_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_RESERVED = re.compile(r"^(con|prn|aux|nul|com\d|lpt\d)(\..*)?$", re.I)

# 名前を受ける命令と、作るファイルの拡張子
NAMED_OUTPUTS = {"save": ".h5", "export_csv": ".csv", "record_finish": ".h5", "finish_unfinished": ".h5",
                 "bandplan_save": ".json", "playback_extract": ".h5", "playback_export_csv": ".csv"}


class WebArgError(Exception):
    def __init__(self, code: str, field: Optional[str] = None, detail: Optional[dict] = None) -> None:
        super().__init__(code)
        self.code = code
        self.field = field
        self.detail = detail or {}

    def to_dict(self) -> dict:
        return {"error": {"code": self.code, "field": self.field, "detail": self.detail}}


def safe_name(name: Optional[str], ext: str) -> str:
    """利用者が付けた名前をファイル名にする（フォルダの区切りや使えない文字は「_」。拡張子が無ければ付ける）。"""
    base = str(name or "").strip()
    base = base.replace("\\", "/").split("/")[-1]
    base = _BAD.sub("_", base).strip(" .")
    if not base:
        base = datetime.datetime.now().strftime("gigast_%Y%m%d_%H%M%S")
    if _RESERVED.match(base):
        base = "_" + base
    if ext and not base.lower().endswith(ext):
        base += ext
    if len(base) > MAX_NAME_CHARS:
        stem = base[: MAX_NAME_CHARS - len(ext)]
        base = stem + ext
    return base


@dataclass
class WebFile:
    file_id: str
    path: Path
    name: str
    task_id: Optional[str] = None
    ready: bool = False


class WebFiles:
    """`web_files\\` の中身（ダウンロードできるファイル・アップロードされたファイル・まとめ終わっていない記録の番号）。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._lock = threading.Lock()
        self._files: dict[str, WebFile] = {}
        self._by_task: dict[str, str] = {}
        self._uploads: dict[str, Path] = {}
        self._unfinished: dict[str, str] = {}  # 番号 → 作業ファイルのパス
        self._unfinished_ids: dict[str, str] = {}  # パス → 番号（同じ記録は同じ番号）

    def reset(self) -> None:
        """起動のときに空にする（前の起動のファイルを残さない）。"""
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True, exist_ok=True)

    # ---- 作るファイル
    def new_output(self, name: Optional[str], ext: str) -> WebFile:
        fid = secrets.token_hex(8)
        folder = self.root / "out" / fid
        folder.mkdir(parents=True, exist_ok=True)
        n = safe_name(name, ext)
        f = WebFile(fid, folder / n, n)
        with self._lock:
            self._files[fid] = f
        return f

    def bind_task(self, fid: str, task_id: Optional[str]) -> None:
        with self._lock:
            f = self._files.get(fid)
            if f is None:
                return
            if task_id:
                f.task_id = task_id
                self._by_task[task_id] = fid
            else:
                f.ready = f.path.exists()

    def task_ended(self, task_id: str, ok: bool) -> Optional[str]:
        """長い処理が終わった。そのファイルの file_id を返す（ダウンロードできるようになる）。"""
        with self._lock:
            fid = self._by_task.pop(task_id, None)
            if fid is None:
                return None
            f = self._files[fid]
            if ok and f.path.exists():
                f.ready = True
                return fid
            self._files.pop(fid, None)
        shutil.rmtree(f.path.parent, ignore_errors=True)
        return None

    def discard_output(self, fid: str) -> None:
        with self._lock:
            f = self._files.pop(fid, None)
        if f is not None:
            shutil.rmtree(f.path.parent, ignore_errors=True)

    def get(self, fid: str) -> Optional[WebFile]:
        with self._lock:
            return self._files.get(fid)

    # ---- アップロード
    def new_upload(self, name: Optional[str]) -> tuple[str, Path]:
        uid = secrets.token_hex(8)
        folder = self.root / "up" / uid
        folder.mkdir(parents=True, exist_ok=True)
        return uid, folder / safe_name(name, "")

    def upload_done(self, uid: str, path: Path) -> None:
        with self._lock:
            self._uploads[uid] = path

    def upload_failed(self, path: Path) -> None:
        shutil.rmtree(path.parent, ignore_errors=True)

    def upload_path(self, uid) -> Path:
        with self._lock:
            p = self._uploads.get(uid) if isinstance(uid, str) else None
        if p is None:
            raise WebArgError("unknown_upload", "upload_id", {"upload_id": uid})
        return p

    # ---- まとめ終わっていない記録
    def unfinished_rows(self, rows: list[dict]) -> list[dict]:
        out = []
        with self._lock:
            for r in rows:
                p = str(r.get("path"))
                uid = self._unfinished_ids.get(p)
                if uid is None:
                    uid = secrets.token_hex(4)
                    self._unfinished_ids[p] = uid
                    self._unfinished[uid] = p
                out.append({"id": uid, **{k: v for k, v in r.items() if k != "path"}})
        return out

    def unfinished_path(self, uid) -> str:
        with self._lock:
            p = self._unfinished.get(uid) if isinstance(uid, str) else None
        if p is None:
            raise WebArgError("unknown_recording", "id", {"id": uid})
        return p


@dataclass
class Translated:
    """Web の口の命令をコアの命令に写したもの。after(戻り値) で戻り値を Web の形にする。"""

    args: dict
    after: Callable[[object], object]
    on_error: Callable[[], None] = lambda: None  # コアが断ったとき（作りかけのフォルダを消す）


def translate(files: WebFiles, command: str, args: dict) -> Translated:
    """Web の口の引数（A §4.3.2 の 3 の表）をコアの引数にする。パスを含む命令は断る。"""
    bad = [k for k in PATH_ARGS if k in args]
    if bad:
        raise WebArgError("path_not_allowed", bad[0])
    args = dict(args)
    ident: Callable[[object], object] = lambda r: r  # noqa: E731

    if command in NAMED_OUTPUTS:
        name = args.pop("name", None)
        if name is not None and not isinstance(name, str):
            raise WebArgError("bad_value", "name")
        if command == "finish_unfinished":
            args["path"] = files.unfinished_path(args.pop("id", None))
            out = files.new_output(name, NAMED_OUTPUTS[command])
            args["h5_path"] = str(out.path)
        else:
            out = files.new_output(name, NAMED_OUTPUTS[command])
            args["path"] = str(out.path)

        def after(r: object, out: WebFile = out) -> object:
            tid = r.get("task_id") if isinstance(r, dict) else None
            files.bind_task(out.file_id, tid)
            if isinstance(r, dict):
                return {**{k: v for k, v in r.items() if k != "path"}, "file_id": out.file_id, "name": out.name}
            return r

        return Translated(args, after, lambda out=out: files.discard_output(out.file_id))

    if command == "open":
        ids = args.pop("uploads", None)
        if not isinstance(ids, list) or not ids:
            raise WebArgError("upload_required", "uploads")
        args["paths"] = [str(files.upload_path(u)) for u in ids]
        return Translated(args, ident)

    if command in ("import_legacy_calibration", "bandplan_load"):
        args["path"] = str(files.upload_path(args.pop("upload_id", None)))
        return Translated(args, ident)

    if command == "discard_unfinished":
        args["path"] = files.unfinished_path(args.pop("id", None))
        return Translated(args, lambda r: {"discarded": True})

    if command == "list_unfinished":
        def after_list(r: object) -> object:
            if isinstance(r, dict) and isinstance(r.get("recordings"), list):
                return {**r, "recordings": files.unfinished_rows(r["recordings"])}
            return r

        return Translated(args, after_list)

    return Translated(args, ident)
