"""
MD-02: credentials must never reach a log handler.

The Webull SDK logs a failed request in full, headers included. These tests
log records shaped like the real ones (fake values) through their own
handlers, including one attached directly to an SDK logger, and check that no
credential value comes out.
"""

import io
import logging
import unittest

from backend.observability.redaction import REDACTED, install_secret_redaction, redact_secrets

_APP_KEY = "fakeappkey0123456789abcdefghijklmno"
_TOKEN = "faketoken0123456789abcdef012345"
_SIGNATURE = "fakesig0123456789abcdef0123456789abcdefghij="

# Shaped like the SDK's ServerException record.
_SDK_MESSAGE = (
    'ServerException occurred. Host:api.webull.com Request:{\n'
    '  "_action_name": "/openapi/market-data/stock/bars",\n'
    '  "_header": {\n'
    f'    "x-app-key": "{_APP_KEY}",\n'
    f'    "x-signature": "{_SIGNATURE}",\n'
    f'    "x-access-token": "{_TOKEN}",\n'
    '    "x-version": "v2"\n'
    "  }\n"
    "} error_code: TOO_MANY_REQUESTS"
)


class TestRedactSecrets(unittest.TestCase):
    def test_header_values_are_replaced_and_the_rest_kept(self):
        out = redact_secrets(_SDK_MESSAGE)
        for secret in (_APP_KEY, _TOKEN, _SIGNATURE):
            self.assertNotIn(secret, out)
        self.assertIn(f'"x-access-token": "{REDACTED}"', out)
        self.assertIn('"x-version": "v2"', out)
        self.assertIn("TOO_MANY_REQUESTS", out)

    def test_other_credential_shapes(self):
        out = redact_secrets(f"access_token={_TOKEN} app_secret: {_APP_KEY} Authorization: Bearer {_TOKEN}")
        self.assertNotIn(_TOKEN, out)
        self.assertNotIn(_APP_KEY, out)

    def test_plain_text_is_untouched(self):
        text = "Fetched 390 bars for SPY; the authorization boundary is unchanged."
        self.assertEqual(redact_secrets(text), text)


class TestLogRecordsAreRedacted(unittest.TestCase):
    def setUp(self):
        install_secret_redaction()
        self.stream = io.StringIO()
        self.handler = logging.StreamHandler(self.stream)
        self.handler.setFormatter(logging.Formatter("%(name)s %(message)s"))

    def _logger(self, name: str) -> logging.Logger:
        logger = logging.getLogger(name)
        logger.addHandler(self.handler)
        logger.setLevel(logging.DEBUG)
        self.addCleanup(logger.removeHandler, self.handler)
        return logger

    def _assert_clean(self):
        output = self.stream.getvalue()
        for secret in (_APP_KEY, _TOKEN, _SIGNATURE):
            self.assertNotIn(secret, output)
        return output

    def test_sdk_logger_with_its_own_handler(self):
        """The SDK writes through handlers it attaches itself; those must see the redacted text."""
        self._logger("webull.core.client").error(_SDK_MESSAGE)
        output = self._assert_clean()
        self.assertIn("ServerException occurred", output)

    def test_sdk_message_passed_as_an_argument(self):
        self._logger("webull.core.http.response").error("request failed: %s", _SDK_MESSAGE)
        self._assert_clean()

    def test_exception_text_is_redacted(self):
        logger = self._logger("webull.core.client")
        try:
            raise RuntimeError(_SDK_MESSAGE)
        except RuntimeError:
            logger.exception("get_response exception")
        self._assert_clean()

    def test_our_own_logger_mentioning_a_token(self):
        self._logger("backend.market_data.providers.webull_provider").warning(
            f'token refresh returned "x-access-token": "{_TOKEN}"'
        )
        self._assert_clean()

    def test_ordinary_records_are_unchanged(self):
        self._logger("backend.market_data.services.ingestion_service").info("wrote %d bars", 42)
        self.assertIn("wrote 42 bars", self.stream.getvalue())


if __name__ == "__main__":
    unittest.main()
