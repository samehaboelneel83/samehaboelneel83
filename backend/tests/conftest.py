from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# The database URL must be set before psp.db.base builds its engine at import time.
_TMP = tempfile.mkdtemp(prefix="psp-tests-")
os.environ["PSP_DATABASE_URL"] = f"sqlite:///{Path(_TMP) / 'test.sqlite3'}"
os.environ["PSP_AUTH_REQUIRED"] = "false"


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from psp.db.base import create_all
    from psp.main import app

    create_all()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def session():
    from psp.db.base import SessionLocal, create_all

    create_all()
    db = SessionLocal()
    try:
        yield db
        db.commit()
    finally:
        db.close()
