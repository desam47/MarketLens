"""Realtime WebSocket push — bars and trend signals."""
from .ws_router import router, broadcast_manager, install as _install, reset as _reset

__all__ = ["router", "broadcast_manager", "install", "reset"]


def install(loop=None):
    return _install(loop)


def reset():
    return _reset()
