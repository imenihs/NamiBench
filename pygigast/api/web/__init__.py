"""Web の口（設計A §4.3）。HTTP の命令・状態と、WebSocket のイベント。"""
from .server import create_app, run_in_thread, serve

__all__ = ["create_app", "run_in_thread", "serve"]
