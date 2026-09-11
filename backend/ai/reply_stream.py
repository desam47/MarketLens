"""
Incremental extraction of the JSON ``"reply"`` string from a growing
raw completion — for streaming the universal chat.

The chat model returns a single JSON object
(``{"reply": "...", "grounded": ..., ...}``), usually wrapped in a
```json fence. Streaming means we only ever have a *prefix* of that
object at any moment, and we want to surface the human-readable
``reply`` text as it arrives without waiting for the closing brace.

``ReplyExtractor.feed(raw)`` is called with the full raw text so far
(cheap: the caller already accumulates it) and returns only the newly
decoded suffix of the ``reply`` value. It tolerates:
  - a leading ```json fence and any prose before the object,
  - JSON string escapes (``\\n \\t \\" \\\\ \\/ \\uXXXX`` …),
  - a chunk boundary landing mid-escape (nothing is emitted until the
    escape can be decoded in full).

Once the closing unescaped ``"`` is seen, ``finished`` is True and
further ``feed`` calls return ``""``.
"""
from __future__ import annotations

import re

_REPLY_KEY_RE = re.compile(r'"reply"\s*:\s*"')
_SIMPLE_ESCAPES = {
    '"': '"', "\\": "\\", "/": "/",
    "n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f",
}


class ReplyExtractor:
    def __init__(self) -> None:
        self._val_start: int | None = None  # index just past the opening quote
        self._pos = 0                       # how far we've decoded into `raw`
        self._decoded = ""
        self.finished = False

    @property
    def text(self) -> str:
        return self._decoded

    def feed(self, raw: str) -> str:
        """Return the newly-decoded suffix of the ``reply`` value (may be "")."""
        if self.finished:
            return ""

        if self._val_start is None:
            m = _REPLY_KEY_RE.search(raw)
            if not m:
                return ""
            self._val_start = m.end()
            self._pos = self._val_start

        i = self._pos
        n = len(raw)
        out: list[str] = []
        while i < n:
            c = raw[i]
            if c == '"':
                self.finished = True
                i += 1
                break
            if c == "\\":
                if i + 1 >= n:
                    break  # need more input to know the escape
                nxt = raw[i + 1]
                if nxt == "u":
                    if i + 6 > n:
                        break  # need the 4 hex digits
                    hexs = raw[i + 2:i + 6]
                    try:
                        out.append(chr(int(hexs, 16)))
                    except ValueError:
                        out.append("\\u" + hexs)
                    i += 6
                    continue
                out.append(_SIMPLE_ESCAPES.get(nxt, nxt))
                i += 2
                continue
            out.append(c)
            i += 1

        self._pos = i
        delta = "".join(out)
        self._decoded += delta
        return delta
