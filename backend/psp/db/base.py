"""Database session management."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from psp.core.config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    settings = get_settings()
    kwargs: dict = {"echo": settings.database_echo, "future": True}
    if settings.database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs["pool_pre_ping"] = True
    engine = create_engine(settings.database_url, **kwargs)
    if settings.database_url.startswith("sqlite"):
        # SQLite ignores foreign keys unless asked, which would let the
        # provenance chain develop holes without anything complaining.
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_all() -> None:
    """Create the schema.

    Convenient for SQLite and tests. PostgreSQL deployments should run the
    migrations in ``migrations/sql`` instead, because those add the ltree,
    PostGIS and partitioning features the ORM cannot express portably.
    """
    from psp.db import models  # noqa: F401 - registers the mappings

    Base.metadata.create_all(engine)
