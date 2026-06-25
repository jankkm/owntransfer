from __future__ import annotations

import secrets
from contextvars import ContextVar

from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CSRF_SESSION_KEY = "csrf_token"
CSRF_FIELD_NAME = "csrf_token"
CSRF_HEADER_NAME = "x-csrf-token"

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

# Largest form body we will buffer in order to read the token from a form field
# when no X-CSRF-Token header is present. JS-driven uploads always send the
# header (and skip this path); only small HTML form posts are buffered.
_MAX_BUFFERED_BODY = 64 * 1024

_csrf_token_var: ContextVar[str] = ContextVar("csrf_token", default="")


def get_csrf_token() -> str:
    return _csrf_token_var.get()


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


def _content_length(scope: Scope) -> int | None:
    raw = Headers(scope=scope).get("content-length")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


async def _empty_receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


async def _buffer_body(receive: Receive) -> tuple[bytes | None, Receive]:
    chunks: list[bytes] = []
    size = 0
    more = True
    while more:
        message = await receive()
        if message["type"] != "http.request":
            # e.g. http.disconnect — nothing more to read.
            break
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > _MAX_BUFFERED_BODY:
            return None, receive
        chunks.append(chunk)
        more = message.get("more_body", False)

    body = b"".join(chunks)

    async def replay() -> Message:
        return {"type": "http.request", "body": body, "more_body": False}

    return body, replay


async def _extract_form_token(scope: Scope, body: bytes, content_type: str) -> str | None:
    if content_type.startswith("application/x-www-form-urlencoded"):
        from urllib.parse import parse_qs

        parsed = parse_qs(body.decode("utf-8", "ignore"))
        values = parsed.get(CSRF_FIELD_NAME)
        return values[0] if values else None

    # multipart/form-data — let Starlette parse it from the buffered body.
    async def replay() -> Message:
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(scope, replay)
    try:
        form = await request.form()
    except Exception:
        return None
    value = form.get(CSRF_FIELD_NAME)
    return value if isinstance(value, str) else None


class CSRFMiddleware:
    """Synchronizer-token CSRF protection for cookie-authenticated requests.

    A per-session token is stored in the signed session and must be echoed back
    on every unsafe request, either via the ``X-CSRF-Token`` header (AJAX) or a
    ``csrf_token`` form field. Must be installed *inside* SessionMiddleware so
    that ``scope['session']`` is populated.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        session = scope.get("session")
        if isinstance(session, dict):
            token = session.get(CSRF_SESSION_KEY)
            if not token:
                token = _generate_token()
                session[CSRF_SESSION_KEY] = token
        else:
            token = _generate_token()

        reset = _csrf_token_var.set(token)
        try:
            method = scope.get("method", "GET").upper()
            if method in SAFE_METHODS:
                await self.app(scope, receive, send)
                return

            headers = Headers(scope=scope)
            submitted = headers.get(CSRF_HEADER_NAME)
            downstream_receive = receive

            if not submitted:
                content_type = headers.get("content-type", "")
                if content_type.startswith(
                    ("application/x-www-form-urlencoded", "multipart/form-data")
                ):
                    content_length = _content_length(scope)
                    if content_length is not None and content_length > _MAX_BUFFERED_BODY:
                        await self._reject(scope, send)
                        return
                    body, downstream_receive = await _buffer_body(receive)
                    if body is None:
                        await self._reject(scope, send)
                        return
                    submitted = await _extract_form_token(scope, body, content_type)

            if (
                not isinstance(session, dict)
                or not token
                or not submitted
                or not secrets.compare_digest(str(submitted), str(token))
            ):
                await self._reject(scope, send)
                return

            await self.app(scope, downstream_receive, send)
        finally:
            _csrf_token_var.reset(reset)

    async def _reject(self, scope: Scope, send: Send) -> None:
        response = PlainTextResponse("CSRF verification failed", status_code=403)
        await response(scope, _empty_receive, send)
