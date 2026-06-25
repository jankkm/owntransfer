from __future__ import annotations

import logging
import secrets

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.logging_config import SETUP_LOGGER_NAME
from app.services.settings import is_setup_complete
_STATE_KEY = "setup_token"


def init_setup_token(app: FastAPI) -> None:
    if settings.setup_token:
        app.state.setup_token = settings.setup_token.strip()
    else:
        app.state.setup_token = secrets.token_urlsafe(32)


def get_setup_token(app: FastAPI) -> str | None:
    return getattr(app.state, _STATE_KEY, None)


def verify_setup_token(app: FastAPI, submitted: str | None) -> bool:
    expected = get_setup_token(app)
    if not expected or not submitted:
        return False
    return secrets.compare_digest(submitted.strip(), expected)


async def log_setup_token_if_needed(app: FastAPI, db: AsyncSession) -> None:
    if await is_setup_complete(db):
        return

    logger = logging.getLogger(SETUP_LOGGER_NAME)
    logger.setLevel(logging.WARNING)

    if settings.setup_token:
        logger.warning(
            "OwnTransfer first-boot setup is open at /setup. "
            "Use the SETUP_TOKEN value from your environment."
        )
        return

    token = get_setup_token(app)
    if not token:
        return

    logger.warning(
        "========================================================================\n"
        "OwnTransfer first-boot setup is waiting at /setup\n"
        "Setup token (required once): %s\n"
        "Copy this token from the container logs and enter it on the setup page.\n"
        "If you restart before finishing setup, check the logs for a new token.\n"
        "========================================================================",
        token,
    )
