"""Realtime WebSocket push — bars and trend signals."""
from .ws_router import broadcast_manager, router
from .ws_router import install as _install
from .ws_router import reset as _reset

__all__ = ["router", "broadcast_manager", "install", "reset"]


def install(loop=None):
    return _install(loop)


def reset():
    return _reset()
