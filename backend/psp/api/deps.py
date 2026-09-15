"""Shared dependencies."""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from psp.core.config import Settings, get_settings
from psp.core.security import current_principal
from psp.db import models as m
from psp.db.base import get_session
from psp.solvers.base import DEFAULT_THREADS, SolveOptions

SessionDep = Depends(get_session)
PrincipalDep = Depends(current_principal)
SettingsDep = Depends(get_settings)


def get_problem(key: str, session: Session = SessionDep) -> m.Problem:
    problem = session.scalar(select(m.Problem).where(m.Problem.key == key))
    if problem is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no problem with key '{key}'"
        )
    return problem


ProblemDep = Depends(get_problem)


def build_options(
    time_limit_seconds: float | None,
    relative_gap: float | None = None,
    threads: int = DEFAULT_THREADS,
    seed: int = 0,
    settings: Settings | None = None,
) -> SolveOptions:
    """Clamp a caller's time limit to the configured maximum.

    An unbounded time limit from an HTTP caller is a denial-of-service waiting
    to happen, so the ceiling is enforced here rather than trusted from input.
    """
    settings = settings or get_settings()
    requested = time_limit_seconds or settings.solve_time_limit_seconds
    return SolveOptions(
        time_limit_seconds=min(max(0.1, requested), settings.solve_max_time_limit_seconds),
        relative_gap=relative_gap,
        threads=max(1, min(threads, 8)),
        seed=seed,
    )
