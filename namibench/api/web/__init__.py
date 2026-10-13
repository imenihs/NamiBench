"""Web の口（設計A §4.3）。HTTP の命令・状態と、WebSocket のイベント。LAN に開くときの決まりは A §4.3.2。"""
from .server import create_app
from .service import WebService

__all__ = ["create_app", "WebService"]
