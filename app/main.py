from __future__ import annotations

from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.database import async_session, engine
from app.http.csrf import CSRFMiddleware
from app.limiter import limiter
from app.logging_config import configure_logging
from app.middleware.public_scheme import PublicSchemeMiddleware
from app.middleware.locale import LocaleMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.middleware.setup import SetupMiddleware
from app.routers import admin, auth, branding, dashboard, locale, profile, public, requests, setup, transfers
from app.services.cleanup import run_cleanup
from app.services.schema import ensure_schema

scheduler = AsyncIOScheduler()


async def _cleanup_job() -> None:
    async with async_session() as db:
        await run_cleanup(db)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await ensure_schema(conn)

    scheduler.add_job(_cleanup_job, "interval", minutes=15, id="cleanup")
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # Added first => innermost: runs after SessionMiddleware has populated
    # scope["session"], which the CSRF token store relies on.
    app.add_middleware(CSRFMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        https_only=settings.cookies_secure,
        same_site="lax",
    )
    app.add_middleware(LocaleMiddleware)
    app.add_middleware(SetupMiddleware)
    app.add_middleware(SlowAPIMiddleware)
    if settings.public_scheme:
        app.add_middleware(PublicSchemeMiddleware)
    if settings.trust_proxy_headers:
        from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

        trusted = settings.trusted_proxy_ip_list or ["*"]
        app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=trusted)

    # Outermost: applied to every response, including /static and /health.
    app.add_middleware(SecurityHeadersMiddleware)

    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    app.include_router(locale.router)
    app.include_router(branding.router)
    app.include_router(setup.router)
    app.include_router(auth.router)
    app.include_router(profile.router)
    app.include_router(dashboard.router)
    app.include_router(transfers.router)
    app.include_router(requests.router)
    app.include_router(public.router)
    app.include_router(admin.router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "db": settings.db_backend}

    @app.get("/ready")
    async def ready() -> dict[str, str]:
        return {"status": "ready"}

    return app


app = create_app()
