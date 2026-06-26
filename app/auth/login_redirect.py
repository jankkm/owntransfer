from __future__ import annotations

from urllib.parse import quote

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.auth.exceptions import NotAuthenticated
from app.http.safe_redirect import safe_next_path

LOGIN_URL = "/auth/login"
DASHBOARD_URL = "/dashboard"
LOGIN_NEXT_SESSION_KEY = "login_next"


def dashboard_redirect() -> RedirectResponse:
    return RedirectResponse(DASHBOARD_URL, status_code=303)


def get_effective_login_next(request: Request, next_param: str | None = None) -> str | None:
    raw = next_param or request.session.get(LOGIN_NEXT_SESSION_KEY)
    if not raw:
        return None
    path = safe_next_path(request, raw, fallback="")
    return path or None


def wants_login_redirect(request: Request) -> bool:
    """True when the client expects an HTML page rather than a JSON API error."""
    dest = request.headers.get("sec-fetch-dest", "")
    mode = request.headers.get("sec-fetch-mode", "")
    if dest == "document" or mode == "navigate":
        return True
    if dest in ("empty", "object"):
        return False

    accept = request.headers.get("accept", "")
    if "application/json" in accept and "text/html" not in accept:
        return False

    if request.method in ("GET", "HEAD"):
        return True

    content_type = request.headers.get("content-type", "")
    if request.method == "POST" and (
        "application/x-www-form-urlencoded" in content_type
        or "multipart/form-data" in content_type
    ):
        return "text/html" in accept or "*/*" in accept or not accept

    return False


def store_login_next(request: Request, next_path: str | None) -> None:
    if not next_path:
        return
    request.session[LOGIN_NEXT_SESSION_KEY] = safe_next_path(request, next_path)


def get_login_target(request: Request, *, fallback: str = "/dashboard") -> str:
    next_param = request.query_params.get("next")
    if next_param:
        return safe_next_path(request, next_param, fallback=fallback)
    session_next = request.session.get(LOGIN_NEXT_SESSION_KEY)
    if session_next:
        return safe_next_path(request, session_next, fallback=fallback)
    return fallback


def clear_login_next(request: Request) -> None:
    request.session.pop(LOGIN_NEXT_SESSION_KEY, None)


def login_redirect_response(request: Request) -> RedirectResponse:
    path = request.url.path
    if request.url.query:
        path = f"{path}?{request.url.query}"
    next_path = safe_next_path(request, path)
    return RedirectResponse(
        f"{LOGIN_URL}?next={quote(next_path, safe='')}",
        status_code=303,
    )


def not_authenticated_response(request: Request, exc: NotAuthenticated) -> RedirectResponse | JSONResponse:
    if wants_login_redirect(request):
        return login_redirect_response(request)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
