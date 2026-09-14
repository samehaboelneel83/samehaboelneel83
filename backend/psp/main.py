"""Application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from psp.api import api_router
from psp.api.ws import router as ws_router
from psp.core.config import get_settings
from psp.db.base import create_all

logger = logging.getLogger("psp")

DESCRIPTION = """
A generic problem-solving platform.

Problems are stated in a business-level **Problem Model**, compiled
deterministically into a solver-independent **Model IR**, and executed by
whichever engine is capable of the resulting model. Every run records the chain
from source data to recommended decision, so any recommendation can be
explained without a language model.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if not settings.is_postgres:
        # SQLite gets its schema created in-process so a first run needs nothing.
        # PostgreSQL deployments run the SQL migrations, which add the ltree,
        # PostGIS and partitioning features the ORM cannot express portably.
        create_all()
    if not settings.auth_required:
        logger.warning(
            "authentication is disabled; every request runs as a development principal"
        )
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        description=DESCRIPTION,
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router)
    app.include_router(ws_router)
    return app


app = create_app()
