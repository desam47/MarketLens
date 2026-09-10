"""Time & Sales (tape) analytics — a rolling per-symbol engine over the
Webull trade-tick stream. See ``backend/tape/tape_engine.py``."""
from backend.tape.tape_engine import TapeEngine

__all__ = ["TapeEngine"]
