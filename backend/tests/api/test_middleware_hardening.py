"""
Regression tests for two middleware-stack fixes in ``backend.api.main`` /
``backend.api.rate_limit``:

* CORS must be the OUTERMOST middleware. Registered first it sat innermost, so
  responses the rate limiter short-circuits (429) never got
  ``Access-Control-Allow-Origin`` and a browser client saw an opaque network
  error instead of a readable 429.
* The rate-limit key must be the connection's peer address, not a
  client-supplied ``X-Forwarded-For`` — otherwise any caller could dodge the
  limit by sending a different header value on every request.
"""
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from backend.api.main import _write_limiter, app
from backend.config.settings import settings


def _allowed_origin() -> str:
    origins = [o for o in settings.cors.allowed_origins if o != "*"]
    return origins[0] if origins else "http://localhost:3000"


class TestCorsOnShortCircuitResponses(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.origin = _allowed_origin()

    def test_429_carries_cors_headers(self):
        with patch.object(_write_limiter, "is_allowed", return_value=(False, 0)):
            r = self.client.post("/api/does-not-exist", headers={"Origin": self.origin})
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.headers.get("access-control-allow-origin"), self.origin)

    def test_cors_is_the_outermost_middleware(self):
        # ``user_middleware`` lists outermost first (Starlette inserts at 0).
        self.assertEqual(app.user_middleware[0].cls.__name__, "CORSMiddleware")

    def test_preflight_still_answered(self):
        r = self.client.options(
            "/api/does-not-exist",
            headers={
                "Origin": self.origin,
                "Access-Control-Request-Method": "POST",
            },
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers.get("access-control-allow-origin"), self.origin)


class TestRateLimitKeyIgnoresForwardedFor(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_rotating_x_forwarded_for_does_not_change_the_limiter_key(self):
        spy = MagicMock(return_value=(True, 29))
        with patch.object(_write_limiter, "is_allowed", spy):
            for spoofed in ("1.1.1.1", "2.2.2.2", "3.3.3.3, 4.4.4.4"):
                self.client.post("/api/does-not-exist", headers={"X-Forwarded-For": spoofed})
        keys = {call.args[0] for call in spy.call_args_list}
        self.assertEqual(len(spy.call_args_list), 3)
        self.assertEqual(len(keys), 1, f"limiter saw multiple keys: {keys}")
        self.assertFalse(keys & {"1.1.1.1", "2.2.2.2", "3.3.3.3"})

    def test_per_endpoint_dependency_uses_the_same_key(self):
        from backend.api.rate_limit import _client_ip

        request = MagicMock()
        request.headers = {"x-forwarded-for": "9.9.9.9"}
        request.client.host = "127.0.0.1"
        self.assertEqual(_client_ip(request), "127.0.0.1")

        request.client = None
        self.assertEqual(_client_ip(request), "unknown")


if __name__ == "__main__":
    unittest.main()
