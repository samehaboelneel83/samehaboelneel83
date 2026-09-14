"""Authentication against Keycloak.

Tokens are verified against the realm's published JWKS. The platform never sees
a password and never issues a token of its own: Keycloak owns identity, this
module only checks signatures and reads claims.

With ``PSP_AUTH_REQUIRED`` unset the API runs open and every request is
attributed to a development principal. That is deliberate for a first run, and
:func:`psp.api.routes_system.health` reports it so an open deployment cannot go
unnoticed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, Request, status

from psp.core.config import Settings, get_settings

_JWKS_CACHE: dict[str, tuple[float, dict]] = {}
JWKS_TTL_SECONDS = 300


@dataclass
class Principal:
    subject: str
    username: str
    roles: list[str] = field(default_factory=list)
    organization: str | None = None
    authenticated: bool = True

    def require_role(self, role: str) -> None:
        if role not in self.roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"this action requires the '{role}' role",
            )


DEVELOPMENT_PRINCIPAL = Principal(
    subject="dev", username="development", roles=["analyst", "operator", "administrator"],
    authenticated=False,
)


def _fetch_jwks(url: str) -> dict:
    cached = _JWKS_CACHE.get(url)
    if cached and time.monotonic() - cached[0] < JWKS_TTL_SECONDS:
        return cached[1]
    import httpx

    response = httpx.get(url, timeout=5.0)
    response.raise_for_status()
    jwks = response.json()
    _JWKS_CACHE[url] = (time.monotonic(), jwks)
    return jwks


def current_principal(
    request: Request, settings: Settings = Depends(get_settings)
) -> Principal:
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""

    if not settings.auth_required and not token:
        return DEVELOPMENT_PRINCIPAL

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="a bearer token is required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    jwks_url = settings.jwks_url
    if not jwks_url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "authentication is required but no Keycloak issuer is configured; "
                "set PSP_KEYCLOAK_ISSUER"
            ),
        )

    from jose import jwt
    from jose.exceptions import JWTError

    try:
        claims = jwt.decode(
            token,
            _fetch_jwks(jwks_url),
            audience=settings.keycloak_audience,
            issuer=settings.keycloak_issuer,
            options={"verify_at_hash": False},
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"token rejected: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    realm_roles = (claims.get("realm_access") or {}).get("roles", [])
    client_roles = (
        (claims.get("resource_access") or {}).get(settings.keycloak_audience, {})
    ).get("roles", [])

    return Principal(
        subject=claims.get("sub", ""),
        username=claims.get("preferred_username") or claims.get("sub", ""),
        roles=sorted({*realm_roles, *client_roles}),
        organization=claims.get("organization"),
    )
