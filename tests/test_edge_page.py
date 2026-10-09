"""試験で使うヘッドレスの Edge（tests/edge_page.py）が、閉じたとき・開く途中で落ちたとき・試験の Python ごと止められた
ときに、Edge のプロセス（本体と子）を1つも残さないことを確かめる。数えるのはその Edge の利用者のフォルダをコマンド行に
持つ msedge だけ（利用者が開いている Edge は数えない）。"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests import edge_page
from tests.edge_page import EdgePage, edge_available, edge_processes

ROOT = Path(__file__).resolve().parents[1]


def _gone(profile: str, timeout: float = 15) -> list[int]:
    """その利用者のフォルダの Edge が全部終わるまで待ち、残っているプロセスの番号を返す（空なら残っていない）。"""
    end = time.monotonic() + timeout
    left = edge_processes(profile)
    while left and time.monotonic() < end:
        time.sleep(0.5)
        left = edge_processes(profile)
    return left


@unittest.skipUnless(edge_available(), "Microsoft Edge が無い")
class EdgeTreeTest(unittest.TestCase):
    def test_close_leaves_no_process(self):
        page = EdgePage()
        try:
            self.assertEqual(page.ev("1 + 1"), 2)
            # 本体のほかに子のプロセス（描画・GPU・クラッシュの記録など）がある
            self.assertGreater(len(edge_processes(page.profile)), 1)
        finally:
            page.close()
        self.assertEqual(_gone(page.profile), [])
        self.assertFalse(Path(page.profile).exists())

    def test_failed_open_leaves_no_process(self):
        """Edge は立ったが DevTools の口に繋げずに開くのが落ちた: 呼んだ側は閉じられないので、開く側が木ごと止める。"""
        made: list[str] = []
        real = tempfile.mkdtemp

        def mkdtemp(prefix):
            made.append(real(prefix=prefix))
            return made[-1]

        def refuse(*_a, **_k):
            # 繋ぐ前に Edge の子が立ち上がりきるのを待つ（立ち上がった子も残らないことを確かめる）
            end = time.monotonic() + 10
            while len(edge_processes(made[-1])) < 2 and time.monotonic() < end:
                time.sleep(0.2)
            raise OSError("繋げない（試験でわざと）")

        with mock.patch.object(edge_page.tempfile, "mkdtemp", mkdtemp), \
                mock.patch("websockets.sync.client.connect", refuse):
            with self.assertRaises(OSError):
                EdgePage()
        self.assertEqual(len(made), 1)
        self.assertEqual(_gone(made[0]), [])

    def test_killed_python_leaves_no_process(self):
        """試験の Python が時間切れなどで止められても（閉じる処理が動かない）、Edge の木は残らない。"""
        code = ("import sys, time; sys.path.insert(0, sys.argv[1]); from tests.edge_page import EdgePage; "
                "p = EdgePage(); print(p.profile, flush=True); time.sleep(120)")
        child = subprocess.Popen([sys.executable, "-c", code, str(ROOT)], stdout=subprocess.PIPE, text=True, cwd=ROOT)
        try:
            profile = child.stdout.readline().strip()
            self.assertTrue(profile)
            self.assertGreater(len(edge_processes(profile)), 1)
        finally:
            child.kill()  # 閉じる処理を通さずに止める
            child.wait(10)
            child.stdout.close()
        self.assertEqual(_gone(profile), [])
        shutil.rmtree(profile, ignore_errors=True)  # 止めた Python が消せなかった利用者のフォルダ


if __name__ == "__main__":
    unittest.main()
