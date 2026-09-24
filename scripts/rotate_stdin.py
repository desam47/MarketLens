#!/usr/bin/env python3
"""
Rotate a process's console output into a size-capped file (MD-05).

``restart_dev.sh`` redirects uvicorn's, craco's, and the RQ workers' stdout
and stderr into ``logs/backend.log``, ``logs/frontend.log`` and
``logs/rq_workers.log`` with a raw shell ``>>``, which nothing ever rotates.
``logs/marketlens.log``, the app's own structured log, is capped by its
``RotatingFileHandler`` at 50 MB x 5 files (``backend/api/structured_logging.py``)
— found live 2026-09-24: the console copy grew unbounded instead, 442 MB in
11 days (about 40 MB a day), because everything written there also goes
through the app's own handler, which is capped, while this raw copy is not.

Piping through this script gives the same 50 MB x 5 file cap to the raw
copy, without needing a second process to watch the file from outside:

    uvicorn ... 2>&1 | python3 scripts/rotate_stdin.py logs/backend.log

Reads stdin line by line (so a long-running child's incremental output is
written promptly, not buffered until EOF) and rotates exactly like
``logging.handlers.RotatingFileHandler``: when the file would exceed
``--max-bytes``, ``file.(N-1)`` becomes ``file.N`` down to ``file.1``, the
current file becomes ``file.1``, and a fresh file is opened.

Never raises past a single write: an I/O error (a full disk, a removed log
directory) is reported once to the ORIGINAL stderr (captured as fd 2 before
this script's own stdin/stdout are redirected) and the line is dropped, so a
logging problem can never kill the process piping into this — the same
promise ``RotatingFileHandler`` itself makes for in-process logging.
"""

from __future__ import annotations

import argparse
import os
import sys

DEFAULT_MAX_BYTES = 50 * 1024 * 1024  # matches structured_logging.py's RotatingFileHandler
DEFAULT_BACKUP_COUNT = 5


_real_stderr = os.fdopen(os.dup(2), "w", buffering=1)


class _Rotator:
    def __init__(self, path: str, max_bytes: int, backup_count: int) -> None:
        self._path = path
        self._max_bytes = max_bytes
        self._backup_count = backup_count
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._fh = open(path, "a", buffering=1, encoding="utf-8", errors="replace")

    def _rotate(self) -> None:
        self._fh.close()
        for i in range(self._backup_count - 1, 0, -1):
            src, dst = f"{self._path}.{i}", f"{self._path}.{i + 1}"
            if os.path.exists(src):
                os.replace(src, dst)
        if os.path.exists(self._path):
            os.replace(self._path, f"{self._path}.1")
        self._fh = open(self._path, "a", buffering=1, encoding="utf-8", errors="replace")

    def write_line(self, line: str) -> None:
        try:
            # fstat, not .tell(): a text-mode file's tell() is an opaque cookie in
            # general, not necessarily a byte count, so it's not safe to add a
            # byte length to it.
            size = os.fstat(self._fh.fileno()).st_size
            if size + len(line.encode("utf-8", errors="replace")) > self._max_bytes:
                self._rotate()
            self._fh.write(line)
        except (OSError, ValueError) as e:
            # ValueError: "I/O operation on closed file" — the fd was closed out from
            # under us (e.g. mid-shutdown). Report and drop the line, same as an OSError.
            print(
                f"rotate_stdin: write to {self._path} failed, dropping a line: {e}",
                file=_real_stderr,
            )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", help="file to write stdin into")
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    ap.add_argument("--backup-count", type=int, default=DEFAULT_BACKUP_COUNT)
    args = ap.parse_args()

    rotator = _Rotator(args.path, args.max_bytes, args.backup_count)
    try:
        for line in sys.stdin:
            rotator.write_line(line)
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
