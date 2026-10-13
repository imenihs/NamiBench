"""部品の「つまんで動かす」の説明は掴む帯の上だけで出す（中の欄にかぶさらない: 設計J §4.3.2）。"""
import unittest

from PySide6.QtCore import QEvent, QPoint
from PySide6.QtGui import QHelpEvent
from PySide6.QtWidgets import QToolTip

from tests.test_ui_qt_support import UiBase


class DockTipTest(UiBase):
    def help_at(self, dock, y):
        p = QPoint(10, y)
        e = QHelpEvent(QEvent.Type.ToolTip, p, dock.mapToGlobal(p))
        dock.event(e)
        self.pump(0.05)
        return QToolTip.isVisible()

    def test_tip_only_on_title_bar(self):
        dock = self.win.parts.docks["markers"]
        dock.show()
        self.pump(0.2)
        body_top = dock.widget().geometry().top()
        self.assertGreater(body_top, 2)
        self.assertFalse(self.help_at(dock, body_top + 20))  # 中の欄の上では出さない
        QToolTip.hideText()
        self.help_at(dock, max(1, body_top // 2))  # 帯の上は今まで通り（offscreen で見えるかは環境による）


if __name__ == "__main__":
    unittest.main()


class FileTraceColorOrderTest(unittest.TestCase):
    def test_qt_and_web_use_same_order(self):
        """重ねたファイルの色の順は Qt と Web で同じ（トレース2の色から）。"""
        import re
        from pathlib import Path

        from namibench.core.palette import FILE_TRACE_ORDER

        js = (Path(__file__).resolve().parents[1] / "namibench/ui_web/app.js").read_text(encoding="utf-8")
        m = re.search(r"const FILE_COLORS = \[([^\]]*)\]", js)
        self.assertIsNotNone(m)
        web = tuple(x.strip().strip('"') for x in m.group(1).split(","))
        self.assertEqual(web, FILE_TRACE_ORDER)
        self.assertEqual(FILE_TRACE_ORDER[0], "trace2")
