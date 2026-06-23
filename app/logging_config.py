from __future__ import annotations

import logging
import sys

SECURITY_LOGGER_NAME = "owntransfer.security"


def configure_logging() -> None:
    if getattr(configure_logging, "_configured", False):
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(handler)
        root.setLevel(logging.INFO)

    security_logger = logging.getLogger(SECURITY_LOGGER_NAME)
    security_logger.setLevel(logging.WARNING)
    if not security_logger.handlers:
        security_logger.addHandler(handler)
    security_logger.propagate = False

    configure_logging._configured = True  # type: ignore[attr-defined]
