from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.config.settings import settings


@dataclass
class OAuthProviderConfig:
    key: str
    name: str
    client_id: str
    client_secret: str
    server_metadata_url: str
    scope: str = "openid email profile"


def get_oauth_providers() -> list[OAuthProviderConfig]:
    providers: list[OAuthProviderConfig] = []
    if settings.entra_client_id and settings.entra_client_secret and settings.entra_tenant_id:
        providers.append(
            OAuthProviderConfig(
                key="entra",
                name="Microsoft",
                client_id=settings.entra_client_id,
                client_secret=settings.entra_client_secret,
                server_metadata_url=(
                    f"https://login.microsoftonline.com/{settings.entra_tenant_id}"
                    "/v2.0/.well-known/openid-configuration"
                ),
            )
        )
    return providers
