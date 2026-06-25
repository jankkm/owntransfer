from __future__ import annotations

import logging

from app.config import settings
from app.logging_config import CONFIG_LOGGER_NAME


def warn_unrestricted_proxy_trust() -> None:
    """Log when forwarded client IPs are accepted from any peer."""
    if not settings.trust_proxy_headers:
        return
    if settings.trusted_proxy_ip_list:
        return

    logging.getLogger(CONFIG_LOGGER_NAME).warning(
        "TRUST_PROXY_HEADERS is enabled but TRUSTED_PROXY_IPS is empty. "
        "All clients can spoof X-Forwarded-For / X-Real-IP, bypassing rate limits "
        "and polluting audit logs. Set TRUSTED_PROXY_IPS to your reverse proxy "
        "addresses (for example 127.0.0.1 or the Docker network CIDR)."
    )
