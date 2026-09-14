"""Health, capability and solver-registry endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from psp.core.config import Settings, get_settings
from psp.core.security import Principal, current_principal
from psp.solvers.registry import describe

router = APIRouter(tags=["system"])


@router.get("/health")
def health(settings: Settings = Depends(get_settings)) -> dict:
    """Report what is actually configured, not what is intended.

    ``authentication`` is reported explicitly because an API that silently
    accepts unauthenticated requests is the kind of thing that survives to
    production unnoticed.
    """
    return {
        "status": "ok",
        "application": settings.app_name,
        "environment": settings.environment,
        "database": "postgresql" if settings.is_postgres else "sqlite",
        "authentication": (
            "keycloak" if settings.auth_required and settings.keycloak_issuer else "disabled"
        ),
        "warnings": (
            []
            if settings.auth_required
            else ["authentication is disabled; every request runs as a development principal"]
        ),
    }


@router.get("/solvers")
def solvers() -> dict:
    """The solver registry, with capabilities, without importing any engine."""
    return {
        "solvers": describe(),
        "execution": "each solve runs in an isolated worker process",
    }


@router.get("/me")
def me(principal: Principal = Depends(current_principal)) -> dict:
    return {
        "subject": principal.subject,
        "username": principal.username,
        "roles": principal.roles,
        "organization": principal.organization,
        "authenticated": principal.authenticated,
    }
