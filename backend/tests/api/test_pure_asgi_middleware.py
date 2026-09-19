"""
Contract tests for the four plain-ASGI middleware layers (request counter, correlation
id, security headers, rate limiter).

They were BaseHTTPMiddleware subclasses, each costing ~180-190 us per request
(measured: a task group + memory streams + a response wrapper per layer, ~90% of a
polled request's latency) for what is a counter bump or a header-list edit. These
tests drive the layers as raw ASGI apps and pin the externally visible behaviour, so
the rewrite is provably equivalent: apart from the structural check at the bottom
they hold for the old implementation too.
"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from starlette.middleware.base import BaseHTTPMiddleware

from backend.api.rate_limit import InMemoryRateLimiter, RateLimitMiddleware
from backend.api.security_headers import SecurityHeadersMiddleware
from backend.api.system.router import RequestCounterMiddleware
from backend.observability.correlation_id import CorrelationIdMiddleware
from backend.observability.logging_enhanced import get_correlation_id


# ----------------------------------------------------------------- harness
async def call(app, *, kind="http", method="GET", path="/x", headers=None, client=("1.2.3.4", 1)):
    scope = {
        "type": kind, "method": method, "path": path, "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
    }
    if client is not None:
        scope["client"] = client
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return scope, sent


def start(sent):
    return next(m for m in sent if m["type"] == "http.response.start")


def hdrs(sent) -> dict:
    return {k.decode().lower(): v.decode() for k, v in start(sent)["headers"]}


def raw_hdrs(sent) -> list:
    return [(k.decode().lower(), v.decode()) for k, v in start(sent)["headers"]]


def make_app(extra_headers=(), status=200, body=b"ok", record=None):
    # Header NAMES must be lowercase bytes (ASGI spec; Starlette's Response always does this).
    async def app(scope, receive, send):
        if record is not None:
            record(scope)
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"text/plain"), *extra_headers]})
        await send({"type": "http.response.body", "body": body})
    return app


async def raising_app(scope, receive, send):
    raise RuntimeError("boom")


# ----------------------------------------------------------- request counter
class TestRequestCounter(unittest.IsolatedAsyncioTestCase):
    async def test_counts_http_requests_and_leaves_the_response_alone(self):
        with patch("backend.api.system.router.record_http_request") as rec:
            app = RequestCounterMiddleware(make_app())
            _, sent = await call(app)
            await call(app, method="POST")
        self.assertEqual(rec.call_count, 2)
        self.assertEqual(start(sent)["status"], 200)
        self.assertEqual(sent[1]["body"], b"ok")
        self.assertEqual(hdrs(sent), {"content-type": "text/plain"})

    async def test_websocket_and_lifespan_scopes_are_not_counted(self):
        seen = []
        async def downstream(scope, receive, send):
            seen.append(scope["type"])
        with patch("backend.api.system.router.record_http_request") as rec:
            app = RequestCounterMiddleware(downstream)
            await call(app, kind="websocket")
            await call(app, kind="lifespan")
        self.assertEqual(rec.call_count, 0)
        self.assertEqual(seen, ["websocket", "lifespan"])  # still passed through

    async def test_exceptions_propagate(self):
        with patch("backend.api.system.router.record_http_request"):
            with self.assertRaises(RuntimeError):
                await call(RequestCounterMiddleware(raising_app))


# ------------------------------------------------------------ correlation id
class TestSetCorrelationIdClears(unittest.TestCase):
    def test_none_actually_clears_the_context_var(self):
        """It used to be ctx.reset(ctx.set(None)) — a no-op that restored the old value."""
        from backend.observability.logging_enhanced import set_correlation_id

        def scenario():
            set_correlation_id("abc")
            self.assertEqual(get_correlation_id(), "abc")
            set_correlation_id(None)
            self.assertIsNone(get_correlation_id())
            set_correlation_id("def")
            set_correlation_id("")
            self.assertIsNone(get_correlation_id())

        import contextvars
        contextvars.copy_context().run(scenario)   # don't touch the test runner's own context


class TestCorrelationId(unittest.IsolatedAsyncioTestCase):
    async def test_generates_an_id_when_absent_and_returns_it_in_the_header(self):
        app = CorrelationIdMiddleware(make_app(), generator=lambda: "generated-1")
        _, sent = await call(app)
        self.assertEqual(hdrs(sent)["x-correlation-id"], "generated-1")

    async def test_reuses_the_incoming_id_case_insensitively(self):
        app = CorrelationIdMiddleware(make_app(), generator=lambda: "unused")
        _, sent = await call(app, headers={"X-Correlation-ID": "from-client"})
        self.assertEqual(hdrs(sent)["x-correlation-id"], "from-client")

    async def test_id_is_on_request_state_and_visible_to_logging_during_the_request(self):
        captured = {}

        def record(scope):
            captured["state"] = dict(scope.get("state", {}))
            captured["ctx"] = get_correlation_id()

        app = CorrelationIdMiddleware(make_app(record=record), generator=lambda: "abc")
        scope, _ = await call(app)
        self.assertEqual(captured["state"], {"correlation_id": "abc"})
        self.assertEqual(captured["ctx"], "abc")
        self.assertIsNone(get_correlation_id(), "must be reset once the request ends")

    async def test_context_is_reset_even_when_the_app_raises(self):
        app = CorrelationIdMiddleware(raising_app, generator=lambda: "zzz")
        with self.assertRaises(RuntimeError):
            await call(app)
        self.assertIsNone(get_correlation_id())

    async def test_replaces_a_same_named_header_set_downstream_instead_of_duplicating(self):
        app = CorrelationIdMiddleware(
            make_app(extra_headers=[(b"x-correlation-id", b"downstream")]), generator=lambda: "mine")
        _, sent = await call(app)
        ids = [v for k, v in raw_hdrs(sent) if k == "x-correlation-id"]
        self.assertEqual(ids, ["mine"])

    async def test_update_request_header_false_adds_no_header(self):
        app = CorrelationIdMiddleware(make_app(), update_request_header=False, generator=lambda: "q")
        _, sent = await call(app)
        self.assertNotIn("x-correlation-id", hdrs(sent))

    async def test_websocket_scope_is_untouched(self):
        async def downstream(scope, receive, send):
            self.assertNotIn("state", scope)
        await call(CorrelationIdMiddleware(downstream), kind="websocket")

    async def test_concurrent_requests_keep_their_own_ids(self):
        seen = {}

        async def downstream(scope, receive, send):
            mine = get_correlation_id()
            await asyncio.sleep(0.01)                     # let the others interleave
            seen[mine] = get_correlation_id()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        app = CorrelationIdMiddleware(downstream)
        await asyncio.gather(*[call(app, headers={"x-correlation-id": f"id-{i}"}) for i in range(25)])
        self.assertEqual(seen, {f"id-{i}": f"id-{i}" for i in range(25)})


# ----------------------------------------------------------- security headers
def _sec_settings(**over):
    base = dict(hsts_enabled=False, hsts_max_age_seconds=100, hsts_include_subdomains=False,
                hsts_preload=False, csp_enabled=False, csp_value="", x_frame_options="DENY",
                x_content_type_options=True, referrer_policy="no-referrer", permissions_policy="")
    base.update(over)
    return SimpleNamespace(**base)


class TestSecurityHeaders(unittest.IsolatedAsyncioTestCase):
    async def test_adds_the_baseline_and_leaves_status_and_body_alone(self):
        app = SecurityHeadersMiddleware(make_app(status=201, body=b"made"), settings_obj=_sec_settings())
        _, sent = await call(app)
        h = hdrs(sent)
        self.assertEqual(start(sent)["status"], 201)
        self.assertEqual(sent[1]["body"], b"made")
        self.assertEqual(h["x-frame-options"], "DENY")
        self.assertEqual(h["x-content-type-options"], "nosniff")
        self.assertEqual(h["referrer-policy"], "no-referrer")
        self.assertEqual(h["cross-origin-resource-policy"], "same-origin")
        self.assertEqual(h["content-type"], "text/plain")

    async def test_never_clobbers_or_duplicates_a_header_the_app_already_set(self):
        app = SecurityHeadersMiddleware(
            make_app(extra_headers=[(b"x-frame-options", b"SAMEORIGIN")]), settings_obj=_sec_settings())
        _, sent = await call(app)
        frames = [v for k, v in raw_hdrs(sent) if k == "x-frame-options"]
        self.assertEqual(frames, ["SAMEORIGIN"])

    async def test_settings_are_read_per_response_not_frozen_at_startup(self):
        cfg = _sec_settings(x_frame_options="DENY")
        app = SecurityHeadersMiddleware(make_app(), settings_obj=cfg)
        _, first = await call(app)
        cfg.x_frame_options = "SAMEORIGIN"
        _, second = await call(app)
        self.assertEqual(hdrs(first)["x-frame-options"], "DENY")
        self.assertEqual(hdrs(second)["x-frame-options"], "SAMEORIGIN")

    async def test_optional_headers_follow_their_flags(self):
        on = SecurityHeadersMiddleware(make_app(), settings_obj=_sec_settings(
            hsts_enabled=True, hsts_include_subdomains=True, hsts_preload=True,
            csp_enabled=True, csp_value="default-src 'self'"))
        _, sent = await call(on)
        self.assertEqual(hdrs(sent)["strict-transport-security"], "max-age=100; includeSubDomains; preload")
        self.assertEqual(hdrs(sent)["content-security-policy"], "default-src 'self'")
        off = SecurityHeadersMiddleware(make_app(), settings_obj=_sec_settings())
        _, sent = await call(off)
        self.assertNotIn("strict-transport-security", hdrs(sent))
        self.assertNotIn("content-security-policy", hdrs(sent))

    async def test_a_response_start_without_a_headers_key_still_works(self):
        async def bare(scope, receive, send):
            await send({"type": "http.response.start", "status": 204})
            await send({"type": "http.response.body", "body": b""})
        _, sent = await call(SecurityHeadersMiddleware(bare, settings_obj=_sec_settings()))
        self.assertEqual(hdrs(sent)["x-frame-options"], "DENY")

    async def test_websocket_scope_is_untouched_and_static_headers_still_exposed(self):
        seen = []
        async def downstream(scope, receive, send):
            seen.append(scope["type"])
        await call(SecurityHeadersMiddleware(downstream), kind="websocket")
        self.assertEqual(seen, ["websocket"])
        self.assertIn("Cross-Origin-Resource-Policy", SecurityHeadersMiddleware._static_headers())


# ---------------------------------------------------------------- rate limiter
class TestRateLimiterMiddleware(unittest.IsolatedAsyncioTestCase):
    def _mw(self, limit=3, downstream=None, limiter=None):
        limiter = limiter or InMemoryRateLimiter(max_requests=limit, window_seconds=60)
        return RateLimitMiddleware(downstream or make_app(), limiter=limiter), limiter

    async def test_only_write_methods_consult_the_limiter(self):
        limiter = MagicMock()
        limiter.is_allowed.return_value = (True, 5)
        limiter.max_requests, limiter.window_seconds = 10, 60
        mw, _ = self._mw(limiter=limiter)
        for method in ("GET", "HEAD", "OPTIONS"):
            await call(mw, method=method, path="/api/data")
        self.assertEqual(limiter.is_allowed.call_count, 0)
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            await call(mw, method=method, path="/api/data")
        self.assertEqual(limiter.is_allowed.call_count, 4)

    async def test_websocket_and_lifespan_scopes_pass_straight_through(self):
        limiter = MagicMock()
        seen = []
        async def downstream(scope, receive, send):
            seen.append(scope["type"])
        mw = RateLimitMiddleware(downstream, limiter=limiter)
        await call(mw, kind="websocket")
        await call(mw, kind="lifespan")
        self.assertEqual(seen, ["websocket", "lifespan"])
        limiter.is_allowed.assert_not_called()

    async def test_429_short_circuits_with_json_retry_after_and_security_headers(self):
        called = []
        mw, limiter = self._mw(limit=1, downstream=make_app(record=lambda s: called.append(1)))
        await call(mw, method="POST", path="/api/w")            # uses the budget
        scope, sent = await call(mw, method="POST", path="/api/w")
        h = hdrs(sent)
        self.assertEqual(start(sent)["status"], 429)
        self.assertEqual(len(called), 1, "downstream must not run for a rejected request")
        self.assertEqual(h["retry-after"], "60")
        self.assertEqual(h["x-ratelimit-limit"], "1")
        self.assertEqual(h["x-ratelimit-remaining"], "0")
        self.assertEqual(h["x-content-type-options"], "nosniff")     # inlined security baseline
        self.assertIn(b"Too many requests", sent[1]["body"])

    async def test_allowed_response_keeps_downstream_headers_and_gains_the_budget_headers(self):
        mw, _ = self._mw(limit=5, downstream=make_app(
            extra_headers=[(b"x-ratelimit-remaining", b"stale"), (b"x-custom", b"1")]))
        _, sent = await call(mw, method="POST", path="/api/w")
        h = raw_hdrs(sent)
        self.assertEqual([v for k, v in h if k == "x-ratelimit-remaining"], ["4"])   # replaced, not duplicated
        self.assertEqual([v for k, v in h if k == "x-ratelimit-limit"], ["5"])
        self.assertIn(("x-custom", "1"), h)

    async def test_exempt_paths_skip_the_limiter_even_for_writes(self):
        limiter = MagicMock()
        mw, _ = self._mw(limiter=limiter)
        for path in ("/api/health", "/docs", "/openapi.json", "/redoc", "/api/system/status"):
            await call(mw, method="POST", path=path)
        limiter.is_allowed.assert_not_called()

    async def test_key_is_the_peer_address_or_unknown_without_one(self):
        limiter = MagicMock()
        limiter.is_allowed.return_value = (True, 1)
        limiter.max_requests, limiter.window_seconds = 1, 60
        mw, _ = self._mw(limiter=limiter)
        await call(mw, method="POST", path="/api/w", client=("9.8.7.6", 1))
        await call(mw, method="POST", path="/api/w", client=None)
        self.assertEqual([c.args[0] for c in limiter.is_allowed.call_args_list], ["9.8.7.6", "unknown"])


# ------------------------------------------------------- stacked + streaming
class TestStackedLayers(unittest.IsolatedAsyncioTestCase):
    def _stack(self, downstream):
        app = CorrelationIdMiddleware(downstream, generator=lambda: "sid")
        app = SecurityHeadersMiddleware(app, settings_obj=_sec_settings())
        app = RateLimitMiddleware(app, limiter=InMemoryRateLimiter(10, 60))
        return RequestCounterMiddleware(app)

    async def test_streamed_body_chunks_pass_through_in_order_with_headers_only_on_start(self):
        async def streaming(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            for i, chunk in enumerate((b"a", b"b", b"c")):
                await send({"type": "http.response.body", "body": chunk, "more_body": i < 2})

        with patch("backend.api.system.router.record_http_request"):
            _, sent = await call(self._stack(streaming), method="POST", path="/api/w")
        self.assertEqual([m["type"] for m in sent], ["http.response.start"] + ["http.response.body"] * 3)
        self.assertEqual([m["body"] for m in sent[1:]], [b"a", b"b", b"c"])
        self.assertEqual([m["more_body"] for m in sent[1:]], [True, True, False])
        h = hdrs(sent)
        self.assertEqual(h["x-correlation-id"], "sid")
        self.assertEqual(h["x-frame-options"], "DENY")
        self.assertEqual(h["x-ratelimit-limit"], "10")

    async def test_the_request_body_reaches_the_app_unmodified(self):
        got = []
        async def echo(scope, receive, send):
            got.append((await receive())["body"])
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})
        with patch("backend.api.system.router.record_http_request"):
            await call(self._stack(echo), method="POST", path="/api/w")
        self.assertEqual(got, [b""])


class TestNoLongerBaseHTTPMiddleware(unittest.TestCase):
    """The point of the change: each BaseHTTPMiddleware layer cost ~180-190 us/request."""

    def test_the_four_simple_layers_are_plain_asgi(self):
        for cls in (RequestCounterMiddleware, CorrelationIdMiddleware,
                    SecurityHeadersMiddleware, RateLimitMiddleware):
            self.assertFalse(issubclass(cls, BaseHTTPMiddleware), cls.__name__)


if __name__ == "__main__":
    unittest.main()
