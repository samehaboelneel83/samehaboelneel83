"""Configuration.

Defaults are chosen so the platform starts with no external services at all —
SQLite on disk, authentication off. Every production concern (PostgreSQL,
Keycloak) is switched on by setting an environment variable, not by editing
code, and the API reports which of them are active at ``/api/health``.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PSP_", env_file=".env", extra="ignore")

    app_name: str = "Problem-Solving Platform"
    environment: str = "development"

    # PostgreSQL in production; SQLite keeps a first run dependency-free.
    database_url: str = "sqlite:///./psp.sqlite3"
    database_echo: bool = False

    # Keycloak. With no issuer configured the API runs open, which is correct
    # for local development and must never be the case anywhere else.
    keycloak_issuer: str | None = None
    keycloak_audience: str = "psp-api"
    keycloak_jwks_url: str | None = None
    auth_required: bool = False

    solve_time_limit_seconds: float = 30.0
    solve_max_time_limit_seconds: float = 600.0
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgres")

    @property
    def jwks_url(self) -> str | None:
        if self.keycloak_jwks_url:
            return self.keycloak_jwks_url
        if self.keycloak_issuer:
            return f"{self.keycloak_issuer.rstrip('/')}/protocol/openid-connect/certs"
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
