"""
Redact credentials from log records before any handler sees them.

The Webull SDK logs a failed request in full, headers included, so its
records carried the app key, access token and request signatures into
``logs/backend.log``, ``logs/marketlens.log`` and the SDK's own log files
(MD-02). Its loggers write through their own handlers as well as ours, so a
filter on our handlers alone would miss them.

``install_secret_redaction`` wraps the process-wide log record factory
instead: every record, from any logger and for any handler, is redacted as
it is created. Only records from ``webull*`` loggers, or whose message
mentions a credential key, are formatted and checked, so ordinary records
cost one substring test.
"""

from __future__ import annotations

import logging
import re
from typing import Any

REDACTED = "***"

# Header and field names whose values are credentials.
_SECRET_KEYS = (
    "x-app-key",
    "x-access-token",
    "x-signature",
    "access_token",
    "app_secret",
    "app_key",
    "authorization",
)

# ``"x-access-token": "abc"``, ``'app_secret': 'abc'``, ``access_token=abc``, ``Authorization: Bearer abc``.
_SECRET_VALUE = re.compile(
    r"""(?P<key>["']?(?:"""
    + "|".join(re.escape(key) for key in _SECRET_KEYS)
    + r""")["']?\s*[:=]\s*["']?)(?P<value>(?:Bearer\s+)?[^"'\s,}\]]+)""",
    re.IGNORECASE,
)

_installed = False


def redact_secrets(text: str) -> str:
    """Replace every credential value in ``text`` with ``***``."""
    return _SECRET_VALUE.sub(lambda match: match.group("key") + REDACTED, text)


def _may_hold_secret(record: logging.LogRecord) -> bool:
    if record.name.startswith("webull"):
        return True
    message = record.msg if isinstance(record.msg, str) else ""
    lowered = message.lower()
    return any(key in lowered for key in _SECRET_KEYS)


def _redact_record(record: logging.LogRecord) -> None:
    try:
        message = record.getMessage()
    except Exception:  # noqa: BLE001 - a malformed record is left for logging to report
        return
    redacted = redact_secrets(message)
    if redacted != message:
        record.msg = redacted
        record.args = None
    if record.exc_info and record.exc_info[1] is not None:
        # Formatters reuse ``exc_text`` when it is set, so the traceback is redacted too.
        text = logging.Formatter().formatException(record.exc_info)
        record.exc_text = redact_secrets(text)


def install_secret_redaction() -> None:
    """Redact credentials from every log record created from now on. Idempotent."""
    global _installed
    if _installed:
        return
    previous_factory = logging.getLogRecordFactory()

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = previous_factory(*args, **kwargs)
        if _may_hold_secret(record):
            _redact_record(record)
        return record

    logging.setLogRecordFactory(factory)
    _installed = True
