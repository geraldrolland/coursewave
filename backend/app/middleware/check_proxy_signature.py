"""ASGI middleware that validates requests signed by the API gateway."""

import hashlib
import hmac
import time

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.base_config import Settings


class CheckProxySignatureMiddleware:
    """Reject requests that lack a valid gateway HMAC signature.

    The gateway signs ``"{METHOD}:{URL}:{TIMESTAMP}"`` with HMAC-SHA256 and
    sends the digest in ``x-proxy-signature`` plus the millisecond timestamp in
    ``x-proxy-timestamp``. Signatures outside the configured TTL window are
    rejected, as are tampered signatures.
    """

    EXEMPT_PATHS = frozenset({"/health"})

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings
        self.ttl_ms = settings.proxy_signature_ttl_seconds * 1000

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if scope.get("path") in self.EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        signature = headers.get("x-proxy-signature")
        timestamp_raw = headers.get("x-proxy-timestamp")

        if not signature or not timestamp_raw:
            await self._reject(scope, receive, send, "Missing proxy signature headers")
            return

        try:
            timestamp = int(timestamp_raw)
        except (TypeError, ValueError):
            await self._reject(scope, receive, send, "Invalid proxy timestamp")
            return

        now_ms = int(time.time() * 1000)
        if abs(now_ms - timestamp) >= self.ttl_ms:
            await self._reject(scope, receive, send, "Proxy signature expired")
            return

        payload = f"{scope.get('method', '')}:{self._request_url(scope)}:{timestamp_raw}"
        expected = hmac.new(
            self.settings.proxy_secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected, signature):
            await self._reject(scope, receive, send, "Invalid proxy signature")
            return

        await self.app(scope, receive, send)

    @staticmethod
    def _request_url(scope: Scope) -> str:
        """Rebuild the URL exactly as the gateway signed it.

        Node's ``req.url`` contains the raw path and query string, including
        any path rewriting already applied by the gateway.
        """

        raw_path = scope.get("raw_path")
        if raw_path is not None:
            path = raw_path.decode("latin-1")
        else:
            path = scope.get("path", "")

        query = scope.get("query_string", b"").decode("latin-1")
        return f"{path}?{query}" if query else path

    async def _reject(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        detail: str,
    ) -> None:
        state = scope.setdefault("state", {})
        state["proxy_error"] = detail

        response = JSONResponse(status_code=403, content={"detail": detail})
        await response(scope, receive, send)
