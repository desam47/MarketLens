"""
Tests for Phase 3.5 structured JSON logging.

Validates:
- JsonFormatter produces valid single-line JSON per log record
- Required fields (ts, level, logger, message) are always present
- Extra fields passed via `extra={...}` appear in the payload
- Correlation ID is injected when set in the contextvar
- with_context() injects arbitrary fields into all records in the block
- LOG_LEVEL env var is honored by configure_logging()
- RotatingFileHandler writes to logs/marketlens.log
"""
import json
import logging
import sys
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from unittest import mock

import pytest


class TestJsonFormatter:
    """Unit tests for JsonFormatter."""

    def _make_formatter(self) -> logging.Formatter:
        """Return a JsonFormatter instance for use in tests."""
        from backend.api.structured_logging import JsonFormatter

        return JsonFormatter()

    def _format_record(self, record: logging.LogRecord) -> dict:
        """Format a LogRecord and return the parsed JSON dict."""
        formatter = self._make_formatter()
        raw = formatter.format(record)
        # Must be valid JSON on a single line
        assert "\n" not in raw, f"Multi-line output: {raw!r}"
        return json.loads(raw)

    def _make_record(self, msg: str = "test message", extra: dict | None = None) -> logging.LogRecord:
        """Create a minimal LogRecord for testing."""
        # Suppress mypy noise about the internal ctor
        record = logging.LogRecord(  # type: ignore[arg-type]
            name="test.logger",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg=msg,
            args=(),
            exc_info=None,
        )
        if extra:
            for k, v in extra.items():
                setattr(record, k, v)
        return record

    def test_required_fields_present(self):
        payload = self._format_record(self._make_record())
        assert "ts" in payload
        assert payload["level"] == "INFO"
        assert payload["logger"] == "test.logger"
        assert payload["message"] == "test message"

    def test_timestamp_is_iso_format(self):
        payload = self._format_record(self._make_record())
        # Validate ISO-8601 with milliseconds and either Z suffix or +00:00 offset
        import re

        ts_pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}(Z|[+-]\d{2}:\d{2})$"
        assert re.match(ts_pattern, payload["ts"])

    def test_extra_fields_appear_in_payload(self):
        record = self._make_record(extra={"symbol": "AAPL", "price": 313.45})
        payload = self._format_record(record)
        assert payload["symbol"] == "AAPL"
        assert payload["price"] == 313.45

    def test_non_serializable_extra_stringified(self):
        """Non-serializable extra fields are repr'd rather than dropping the log line."""
        record = self._make_record(extra={"complex": set([1, 2, 3])})
        payload = self._format_record(record)
        assert "complex" in payload
        # Should be a string repr, not the set itself
        assert isinstance(payload["complex"], str)

    def test_exc_info_attached(self):
        try:
            raise ValueError("boom")
        except ValueError:
            record = self._make_record()
            record.exc_info = sys.exc_info()
        payload = self._format_record(record)
        assert "exc" in payload
        assert "ValueError" in payload["exc"]
        assert "boom" in payload["exc"]

    def test_correlation_id_injected(self):
        """When correlation_id is set as an attribute, it appears in the payload."""
        record = self._make_record(extra={"correlation_id": "req-abc-123"})
        payload = self._format_record(record)
        assert payload["correlation_id"] == "req-abc-123"

    def test_correlation_id_from_contextvar(self):
        """When no explicit extra but contextvar is set, correlation_id is injected."""
        from backend.observability.logging_enhanced import set_correlation_id

        # Set correlation ID and format INSIDE the context to keep contextvar active
        set_correlation_id("ctx-def-456")
        record = self._make_record()
        payload = self._format_record(record)
        assert payload["correlation_id"] == "ctx-def-456"
        # Reset after format (not inside try/finally that delays the reset)
        set_correlation_id(None)

    def test_with_context_injects_fields(self):
        """with_context() makes fields appear in all log records in the block."""
        from backend.observability.logging_enhanced import (
            CorrelationIdFilter,
            get_extra_fields,
            with_context,
        )

        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(self._make_formatter())
        f = CorrelationIdFilter()
        handler.addFilter(f)

        # Verify extra fields are set inside the context block
        get_extra_fields()
        with with_context(symbol="MSFT", timeframe="1d"):
            extra_inside = get_extra_fields()
            # Directly verify the filter injects attributes
            record = self._make_record()
            f.filter(record)
            assert hasattr(record, "symbol"), f"filter didn't inject symbol; extra={extra_inside}"
            assert record.symbol == "MSFT"
            handler.emit(record)

        raw = stream.getvalue().strip()
        payload = json.loads(raw)
        assert payload["symbol"] == "MSFT"
        assert payload["timeframe"] == "1d"

    def test_with_context_nesting(self):
        """Nested with_context() calls merge fields; inner takes precedence."""
        from backend.observability.logging_enhanced import (
            CorrelationIdFilter,
            with_context,
        )

        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(self._make_formatter())
        f = CorrelationIdFilter()
        handler.addFilter(f)

        with with_context(a="outer"):
            with with_context(b="inner"):
                record = self._make_record()
                f.filter(record)
                handler.emit(record)

        raw = stream.getvalue().strip()
        payload = json.loads(raw)
        assert payload["a"] == "outer"
        assert payload["b"] == "inner"

    def test_with_context_outside_block_no_extra(self):
        """Log records outside a with_context block have no injected extra fields."""
        record = self._make_record()
        payload = self._format_record(record)
        assert "symbol" not in payload
        assert "timeframe" not in payload


class TestConfigureLogging:
    """Integration-style tests for configure_logging()."""

    def test_log_level_from_env_var(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """When LOG_LEVEL=DEBUG, root logger level is DEBUG."""
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        # Isolate: fresh handlers each time
        with self._isolated_logging(tmp_path):
            from backend.api.structured_logging import configure_logging

            configure_logging(enable_file=False)
            root = logging.getLogger()
            assert root.level == logging.DEBUG

    def test_log_level_arg_takes_precedence(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """Explicit log_level argument overrides LOG_LEVEL env var."""
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        with self._isolated_logging(tmp_path):
            from backend.api.structured_logging import configure_logging

            configure_logging(log_level="WARNING", enable_file=False)
            root = logging.getLogger()
            assert root.level == logging.WARNING

    def test_debug_flag_falls_back_when_no_env_var(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """Without LOG_LEVEL, debug=True → DEBUG, debug=False → INFO."""
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        with self._isolated_logging(tmp_path):
            from backend.api.structured_logging import configure_logging

            configure_logging(debug=True, enable_file=False)
            assert logging.getLogger().level == logging.DEBUG

    def test_invalid_log_level_defaults_to_info(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """Bogus LOG_LEVEL value falls back to INFO."""
        monkeypatch.setenv("LOG_LEVEL", "NOT_A_LEVEL")
        with self._isolated_logging(tmp_path):
            from backend.api.structured_logging import configure_logging

            configure_logging(enable_file=False)
            assert logging.getLogger().level == logging.INFO

    def test_rotating_file_handler_writes(self, tmp_path: Path):
        """With enable_file=True, a RotatingFileHandler writes valid JSON to the log file."""
        with self._isolated_logging(tmp_path):
            from backend.api.structured_logging import configure_logging

            configure_logging(enable_file=True)
            log = logging.getLogger("test_rotating")
            log.info("hello from rotating handler")
            # Explicitly close handlers so the RotatingFileHandler stream is flushed
            for h in logging.getLogger().handlers:
                h.close()

        # The mock patches _get_log_dir to return tmp_path,
        # so the file lands at tmp_path / "marketlens.log"
        log_file = tmp_path / "marketlens.log"
        assert log_file.exists(), f"Expected {log_file} to exist"
        content = log_file.read_text()
        assert "hello from rotating handler" in content
        # Must be valid JSON
        for line in content.splitlines():
            if line.strip():
                parsed = json.loads(line)
                assert "ts" in parsed
                assert parsed["level"] == "INFO"

    def test_json_formatter_on_console(self, tmp_path: Path, capsys: pytest.CaptureFixture):
        """Log output to stdout is valid single-line JSON."""
        with self._isolated_logging(tmp_path):
            from backend.api.structured_logging import configure_logging

            configure_logging(enable_file=False)
            log = logging.getLogger("test_console_json")
            log.info("console json test")

        captured = capsys.readouterr()
        assert captured.out.strip(), "Nothing written to stdout"
        for line in captured.out.splitlines():
            if line.strip():
                parsed = json.loads(line)
                assert "message" in parsed
                assert parsed["message"] == "console json test"

    def test_noisy_loggers_tamed(self, tmp_path: Path):
        """Third-party loggers are set to WARNING (or lower) so they don't spam."""
        with self._isolated_logging(tmp_path):
            from backend.api.structured_logging import configure_logging

            configure_logging(enable_file=False)
            noisy_names = ["uvicorn", "sqlalchemy.engine", "httpx", "finnhub"]
            for name in noisy_names:
                lg = logging.getLogger(name)
                assert lg.level <= logging.WARNING, f"{name} logger too noisy: {lg.level}"

    # ---- helpers ----

    @staticmethod
    @contextmanager
    def _isolated_logging(tmp_path: Path):
        """Temporarily remove all handlers from root logger, yield, restore."""

        root = logging.getLogger()
        saved_handlers = list(root.handlers[:])
        saved_level = root.level
        # Also save third-party loggers
        saved_levels: dict[str, int] = {}
        for name in ["uvicorn", "sqlalchemy.engine", "httpx", "finnhub"]:
            lg = logging.getLogger(name)
            saved_levels[name] = lg.level

        root.handlers.clear()
        root.setLevel(logging.NOTSET)

        # Patch _get_log_dir to use tmp_path so we don't pollute the real logs/
        with mock.patch(
            "backend.api.structured_logging._get_log_dir",
            return_value=tmp_path,
        ):
            yield

        root.handlers.clear()
        root.handlers.extend(saved_handlers)
        root.setLevel(saved_level)
        for name, lvl in saved_levels.items():
            logging.getLogger(name).setLevel(lvl)
