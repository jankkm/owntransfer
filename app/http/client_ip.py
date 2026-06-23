from __future__ import annotations

import ipaddress

from starlette.requests import Request

from app.config import settings


def _parse_ip(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    if value.startswith("[") and "]" in value:
        value = value[1 : value.index("]")]
    elif value.count(":") == 1 and "." in value:
        value = value.rsplit(":", 1)[0]
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def _peer_is_trusted(request: Request) -> bool:
    if not settings.trust_proxy_headers:
        return False
    if not settings.trusted_proxy_ip_list:
        return True
    if not request.client:
        return False
    peer = request.client.host
    for entry in settings.trusted_proxy_ip_list:
        if entry == peer:
            return True
        try:
            if ipaddress.ip_address(peer) in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            continue
    return False


def get_client_ip(request: Request) -> str | None:
    if _peer_is_trusted(request):
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            parsed = _parse_ip(real_ip)
            if parsed:
                return parsed

        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            parts = [part.strip() for part in forwarded_for.split(",") if part.strip()]
            if parts:
                hops = max(1, settings.trusted_proxy_hops)
                index = -hops if len(parts) >= hops else -1
                parsed = _parse_ip(parts[index])
                if parsed:
                    return parsed

    if request.client:
        return request.client.host
    return None
