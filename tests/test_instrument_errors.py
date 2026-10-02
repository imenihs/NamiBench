"""命令の失敗の分け方: 引数の名前と数が命令に合わないときだけ bad_arguments。命令の中で起きた誤りは internal_error と
中身（2026-10-03: 中の型の誤りまで「操作の値が正しくありません」と出て、正しい値を入れた利用者に入力を疑わせた）。"""
import contextlib
import io
import unittest

from pygigast.ui_qt import texts
from tests.instrument_helpers import InstrumentCase


class CommandErrorKindTest(InstrumentCase):
    def test_unknown_argument_is_bad_arguments(self):
        with self.assertRaises(Exception) as cm:
            self.call("set_sweep", spam_hz=1)
        self.assertEqual(cm.exception.code, "bad_arguments")

    def test_fault_inside_a_command_is_internal_error_with_its_content(self):
        def broken(**_kw):
            raise TypeError("中の誤り")

        self.inst._cmd_set_sweep = broken  # わざと壊す（引数の形は合っている）
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(Exception) as cm:
                self.call("set_sweep", span_hz=1_000_000)
        e = cm.exception
        self.assertEqual(e.code, "internal_error")
        self.assertEqual(e.detail["command"], "set_sweep")
        self.assertIn("TypeError: 中の誤り", texts.error_text(e))
        self.assertNotIn(texts.ERRORS["bad_arguments"], texts.error_text(e))


if __name__ == "__main__":
    unittest.main()
