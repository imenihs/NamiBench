"""利用者のデータの場所（設定・覚えた機器・校正・基準・プリセット・配色・記録の作業ファイル）。

- 場所を決めるのはここの data_root() だけ。Windows は %APPDATA%\\NamiBench、それ以外は ~/.namibench
- 名前を変える前の場所（%APPDATA%\\pyGigaSt・~/.pygigast）は prepare_data_root() が起動時に1回だけ見る。
  新しい場所が無く古い場所があれば、中身を丸ごと新しい場所へコピーする（古い場所は消さない・変えない）
- コピーは隣の作業用のフォルダへ書いてから名前を付け替える。途中で失敗したら作業用のフォルダを消し、
  この起動の間は古い場所をそのまま使う（新しい場所を作りかけのまま残さない。次の起動でもう一度コピーする）
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional

APP_DIR = "NamiBench"  # Windows: %APPDATA% の下
HOME_DIR = ".namibench"  # %APPDATA% の無い OS: ホームの下
LEGACY_APP_DIR = "pyGigaSt"  # 名前を変える前の場所
LEGACY_HOME_DIR = ".pygigast"


def data_root(env: Optional[Mapping[str, str]] = None) -> Path:
    """利用者のデータの場所。"""
    env = os.environ if env is None else env
    base = env.get("APPDATA")
    return Path(base) / APP_DIR if base else Path.home() / HOME_DIR


def legacy_data_root(env: Optional[Mapping[str, str]] = None) -> Path:
    """名前を変える前のデータの場所（移すときに読むだけ）。"""
    env = os.environ if env is None else env
    base = env.get("APPDATA")
    return Path(base) / LEGACY_APP_DIR if base else Path.home() / LEGACY_HOME_DIR


@dataclass(frozen=True)
class PreparedRoot:
    """起動時に決めた場所と、そのとき1回だけ出す知らせ（無ければ None）。"""

    path: Path
    notice: Optional[tuple[str, dict]] = None


def prepare_data_root(env: Optional[Mapping[str, str]] = None,
                      copytree: Callable[[Path, Path], object] = shutil.copytree) -> PreparedRoot:
    """起動時に1回呼び、この起動で使うデータの場所を決める。copytree は試験で失敗を起こすためのもの。

    - 新しい場所がある・古い場所が無い → 新しい場所（コピーしない）
    - 新しい場所が無く古い場所がある → コピーして新しい場所。知らせ data_moved
    - コピーに失敗 → 作業用のフォルダを消して古い場所。知らせ data_move_failed（理由付き）
    """
    new, old = data_root(env), legacy_data_root(env)
    if new.exists() or not old.is_dir():
        return PreparedRoot(new)
    # 同時に2つ起動しても作業用のフォルダがぶつからないよう、プロセスの番号を名前に入れる
    work = new.with_name(f"{new.name}.copying-{os.getpid()}")
    detail = {"from": str(old), "to": str(new)}
    try:
        if work.exists():
            shutil.rmtree(work)
        copytree(old, work)
        work.rename(new)
    except OSError as e:
        shutil.rmtree(work, ignore_errors=True)
        if new.is_dir():
            return PreparedRoot(new)  # 同時に起動したもう一方が先に移し終えた（知らせはそちらが出す）
        return PreparedRoot(old, ("data_move_failed", {**detail, "reason": str(e)}))
    return PreparedRoot(new, ("data_moved", detail))
