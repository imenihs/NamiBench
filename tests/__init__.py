"""試験。"""


def release_qt_clipboard_at_exit() -> None:
    """Python が終わる前に、Qt のクリップボードに置いたものを外す（Qt の画面を使う試験のモジュールが QApplication を
    作った後に呼ぶ）。

    画面を出さない試験（offscreen）のクリップボードは Qt の大域の値で、プロセスの一番最後（Python が終わった後）に
    消える。Python で作った QMimeData（PNG のコピー: png_export.copy_png_to_clipboard）が残っていると、それを消す
    ときに終わった Python を呼んでプロセスが落ちた（そのモジュールだけを回すと、試験は全部通るのに終了コードが
    0xC0000005。全体で回すと後の試験がクリップボードを文字で置き換えるので落ちない）。atexit は Python が動いている
    間に、後に登録したものから先に呼ぶので、PySide6 の後片付けより前に外せる。本番の画面（Windows の画面）は
    QApplication を消すときにクリップボードを放すので、この問題は無い。"""
    import atexit

    from PySide6.QtGui import QGuiApplication

    def release() -> None:
        if QGuiApplication.instance() is not None:
            QGuiApplication.clipboard().clear()

    atexit.register(release)
