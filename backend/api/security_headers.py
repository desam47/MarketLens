"""
Security headers middleware for the MarketLens API.

Adds the standard set of HTTP response headers recommended by OWASP and
the Mozilla Web Security guidelines. Each header is opt-out-able via
``SecuritySettings`` so a deployment can disable the ones that don't
apply (e.g. HSTS when the service is reachable over plain HTTP).

Why a middleware instead of a per-endpoint decorator?
- Coverage is uniform: every response gets the headers, including 404s
  raised by FastAPI for unknown paths, error responses from
  middleware, and the auto-generated ``/docs`` and ``/openapi.json``.
- We don't have to remember to add ``response.headers[...]`` in every
  endpoint, which is exactly the kind of thing that gets forgotten.

Header reference:
- HSTS            https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Strict-Transport-Security
- CSP             https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Content-Security-Policy
- X-Frame-Options https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/X-Frame-Options
- X-Content-Type-Options https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/X-Content-Type-Options
- Referrer-Policy https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Referrer-Policy
- Permissions-Policy https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Permissions-Policy
- Cross-Origin-Resource-Policy https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Cross-Origin-Resource-Policy
"""
import logging

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from ..config.settings import settings

logger = logging.getLogger(__name__)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach a baseline of security headers to every outgoing response.

    Existing headers (e.g. set by the application or another middleware)
    are preserved — we only add what's missing. This means the order in
    which middlewares are registered doesn't change the final header
    set; the *last* writer wins, and we never overwrite.
    """

    def __init__(self, app, settings_obj=None) -> None:
        super().__init__(app)
        # ``settings_obj`` is captured for tests. When it's None we read
        # ``settings.security`` lazily on every request so changes to
        # the global settings (e.g. via env-var override) are picked up
        # without rebuilding the middleware stack.
        self._settings_override = settings_obj

    @property
    def _settings(self):
        if self._settings_override is not None:
            return self._settings_override
        return settings.security

    def _build_hsts(self) -> str | None:
        """Render the Strict-Transport-Security header value.

        Returns ``None`` when HSTS is disabled — the header must only
        be sent over HTTPS, otherwise it triggers browser behavior we
        don't want (cached forever, sent to downgrade endpoints).
        """
        if not self._settings.hsts_enabled:
            return None
        value = f"max-age={self._settings.hsts_max_age_seconds}"
        if self._settings.hsts_include_subdomains:
            value += "; includeSubDomains"
        if self._settings.hsts_preload:
            value += "; preload"
        return value

    def _extra_headers(self) -> dict[str, str]:
        """Compute the header dict for the current response.

        The set is small and depends on settings only, so we build it
        once per response rather than caching at startup. This keeps
        tests easy (no need to rebuild the middleware to pick up new
        settings).
        """
        h: dict[str, str] = {}

        hsts = self._build_hsts()
        if hsts is not None:
            h["Strict-Transport-Security"] = hsts

        if self._settings.csp_enabled and self._settings.csp_value:
            h["Content-Security-Policy"] = self._settings.csp_value

        if self._settings.x_frame_options:
            h["X-Frame-Options"] = self._settings.x_frame_options

        if self._settings.x_content_type_options:
            h["X-Content-Type-Options"] = "nosniff"

        if self._settings.referrer_policy:
            h["Referrer-Policy"] = self._settings.referrer_policy

        if self._settings.permissions_policy:
            h["Permissions-Policy"] = self._settings.permissions_policy

        # Lock down cross-origin embedding of API responses by default.
        # The API serves JSON, not assets — same-origin only.
        h["Cross-Origin-Resource-Policy"] = "same-origin"
        h["Cross-Origin-Opener-Policy"] = "same-origin"

        return h

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        for header, value in self._extra_headers().items():
            # Don't clobber headers already set by the application or
            # another middleware.
            if header not in response.headers:
                response.headers[header] = value
        return response

    @classmethod
    def _static_headers(cls, settings_obj=None) -> dict[str, str]:
        """Compute headers for a given settings object (or the global one).

        Used by the rate limiter to attach the same baseline of headers
        to its 429 response — which short-circuits the middleware chain
        and so would otherwise be missing the security headers. Kept as
        a classmethod so callers can pass a custom settings object in
        tests.
        """
        # Temporarily replace the global settings while we build the header
        # dict so the settings lookup is isolated from the caller's env.
        from ..config.settings import settings as _settings

        original = None
        if settings_obj is not None:
            original = _settings.security
            _settings.security = settings_obj
        try:
            return _build_security_header_dict()
        finally:
            if original is not None:
                _settings.security = original


def _build_security_header_dict() -> dict[str, str]:
    """Build the security header dict from the global settings.

    Factored out so it can be called from both the middleware (per-request)
    and from the rate limiter's short-circuit 429 path.
    """
    from ..config.settings import settings

    s = settings.security
    h: dict[str, str] = {}

    if s.hsts_enabled:
        value = f"max-age={s.hsts_max_age_seconds}"
        if s.hsts_include_subdomains:
            value += "; includeSubDomains"
        if s.hsts_preload:
            value += "; preload"
        h["Strict-Transport-Security"] = value

    if s.csp_enabled and s.csp_value:
        h["Content-Security-Policy"] = s.csp_value

    if s.x_frame_options:
        h["X-Frame-Options"] = s.x_frame_options

    if s.x_content_type_options:
        h["X-Content-Type-Options"] = "nosniff"

    if s.referrer_policy:
        h["Referrer-Policy"] = s.referrer_policy

    if s.permissions_policy:
        h["Permissions-Policy"] = s.permissions_policy

    h["Cross-Origin-Resource-Policy"] = "same-origin"
    h["Cross-Origin-Opener-Policy"] = "same-origin"

    return h
