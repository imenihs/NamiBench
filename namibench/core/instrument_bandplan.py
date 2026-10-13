"""計測器の本体のうち、バンドプランとブックマーク（設計P §6・§9.5）。Instrument に混ぜる。

- 中身はアプリのデータの bandplan.json（Qt と Web で共有・次の起動で戻す）。初めは同梱の空の見本と同じ（P §9.1:
  公的な資料は同梱しない）。利用者が作ったファイルを読み込む（bandplan_load）と、確かめてから写す
- ブックマークは画面から足せる・消せる（bookmark_add・bookmark_remove）。周波数を Center にするのは画面が
  set_sweep を送る（プリセットとは別: P §6）
- 出すか出さないかは表示の設定（bandplan_shown・bookmarks_shown）
- ファイルを読む・書くのは書き込みスレッド（設計A §4.2）
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from . import bandplan as bp
from .instrument_common import CommandError

BANDPLAN_FILE = "bandplan.json"


class BandplanMixin:
    """バンドプランとブックマーク（Instrument に混ぜる）。"""

    def _init_bandplan(self) -> None:
        self._bandplan_path = self._root / BANDPLAN_FILE
        self._bandplan = bp.empty()
        self._bandplan_source: Optional[str] = None
        if self._bandplan_path.exists():
            try:
                self._bandplan = bp.load(self._bandplan_path)
                self._bandplan_source = self._settings.get("bandplan_source")
            except bp.BandplanError as e:
                # 読めないときは空で始め、一度知らせる（黙って消さない: ファイルはそのまま残す）
                self._q.put(("notice", "bandplan_unreadable", {"code": e.code, **e.detail}))

    def _bandplan_state(self) -> dict:
        return {"bands": [dict(b) for b in self._bandplan["bands"]],
                "bookmarks": [dict(m) for m in self._bandplan["bookmarks"]], "source": self._bandplan_source}

    def _bp_error(self, e: bp.BandplanError, field: Optional[str] = None) -> CommandError:
        return CommandError(e.code, field, e.detail)

    def _bp_commit(self, plan: dict, source: Optional[str]) -> object:
        """新しい中身をアプリのデータへ書き（書き込みスレッド）、書けたら状態にする。"""
        path = self._bandplan_path

        def then(_p) -> dict:
            self._bandplan = plan
            self._bandplan_source = source
            self._settings.set("bandplan_source", source)
            self._changed()
            return {"bandplan": self._bandplan_state()}

        return self._io(lambda: bp.save(path, plan), then)

    def _cmd_bandplan_load(self, path: str) -> object:
        """利用者が作ったファイルを読み込む（今の中身を置き換える）。読めなければ理由を返して何も変えない。"""
        if not path:
            raise self._error("path_required", "path")
        p = Path(str(path))
        dst = self._bandplan_path

        def job():
            try:
                plan = bp.load(p)
            except bp.BandplanError as e:
                raise self._bp_error(e, "path") from e
            bp.save(dst, plan)
            return plan

        def then(plan) -> dict:
            self._bandplan = plan
            self._bandplan_source = p.name
            self._settings.set("bandplan_source", p.name)
            self._changed()
            return {"bandplan": self._bandplan_state()}

        return self._io(job, then)

    def _cmd_bandplan_save(self, path: str) -> object:
        """いまの中身をファイルに書き出す（利用者が直して読み込むため。何も無ければ空の見本と同じ形）。"""
        if not path:
            raise self._error("path_required", "path")
        plan = {**self._bandplan}
        p = Path(str(path))
        return self._io(lambda: bp.save(p, plan), lambda out: {"path": str(out)})

    def _cmd_bookmark_add(self, name: str, hz: int) -> object:
        try:
            clean = bp.check_name(name)
        except bp.BandplanError as e:
            raise self._bp_error(e, "name") from e
        try:
            f = bp.check_hz(hz)
        except bp.BandplanError as e:
            raise self._bp_error(e, "hz") from e
        marks = list(self._bandplan["bookmarks"]) + [{"name": clean, "hz": f}]
        if len(marks) + len(self._bandplan["bands"]) > bp.MAX_ITEMS:
            raise CommandError("bandplan_too_many", "hz", {"max": bp.MAX_ITEMS})
        plan = bp.parse({**self._bandplan, "bookmarks": marks})
        return self._bp_commit(plan, self._bandplan_source)

    def _cmd_bookmark_remove(self, hz: int, name: Optional[str] = None) -> object:
        marks = list(self._bandplan["bookmarks"])
        for i, m in enumerate(marks):
            if m["hz"] == hz and (name is None or m["name"] == name):
                del marks[i]
                break
        else:
            raise CommandError("unknown_bookmark", "hz", {"hz": hz})
        plan = bp.parse({**self._bandplan, "bookmarks": marks})
        return self._bp_commit(plan, self._bandplan_source)
