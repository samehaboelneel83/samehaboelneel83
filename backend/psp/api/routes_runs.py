"""Runs, solutions, explanations and the provenance chain."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from psp.api.deps import SessionDep
from psp.compiler.pipeline import compile_and_flatten
from psp.db import models as m
from psp.execution.service import load_spec
from psp.provenance.explain import explain_decision
from psp.provenance.solution import Solution

router = APIRouter(tags=["runs"])


@router.get("/runs")
def list_runs(
    problem: str | None = None, limit: int = Query(default=50, le=200),
    session: Session = SessionDep,
) -> dict:
    query = select(m.Run).order_by(m.Run.started_at.desc()).limit(limit)
    if problem:
        problem_row = session.scalar(select(m.Problem).where(m.Problem.key == problem))
        if problem_row is None:
            raise HTTPException(status_code=404, detail=f"no problem with key '{problem}'")
        query = query.where(m.Run.problem_id == problem_row.id)

    runs = session.scalars(query).all()
    solutions = {
        s.run_id: s
        for s in session.scalars(
            select(m.Solution).where(m.Solution.run_id.in_([r.id for r in runs]))
        )
    } if runs else {}

    return {
        "runs": [
            {
                "id": run.id,
                "problem_id": run.problem_id,
                "scenario": run.scenario_key,
                "solver": run.solver_key,
                "status": run.status,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "wall_time_seconds": run.wall_time_seconds,
                "message": run.message,
                "solution_id": solutions[run.id].id if run.id in solutions else None,
                "objectives": solutions[run.id].objective_values if run.id in solutions else [],
            }
            for run in runs
        ]
    }


@router.get("/runs/{run_id}")
def get_run(run_id: str, session: Session = SessionDep) -> dict:
    run = session.get(m.Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"no run '{run_id}'")
    solution = session.scalar(select(m.Solution).where(m.Solution.run_id == run.id))
    version = session.get(m.ModelVersion, run.model_version_id)
    logs = session.scalars(
        select(m.RunLog).where(m.RunLog.run_id == run.id).order_by(m.RunLog.sequence)
    ).all()
    metrics = session.scalars(select(m.RunMetric).where(m.RunMetric.run_id == run.id)).all()
    return {
        "id": run.id,
        "problem_id": run.problem_id,
        "scenario": run.scenario_key,
        "solver": run.solver_key,
        "status": run.status,
        "message": run.message,
        "wall_time_seconds": run.wall_time_seconds,
        "selection_trace": run.selection_trace,
        "model_version": {
            "id": version.id, "version": version.version,
            "fingerprint": version.fingerprint, "statistics": version.statistics,
        } if version else None,
        "solution_id": solution.id if solution else None,
        "logs": [{"sequence": row.sequence, "message": row.message} for row in logs],
        "metrics": [{"key": row.key, "value": row.value} for row in metrics],
    }


@router.get("/solutions/{solution_id}")
def get_solution(solution_id: str, session: Session = SessionDep) -> dict:
    solution = session.get(m.Solution, solution_id)
    if solution is None:
        raise HTTPException(status_code=404, detail=f"no solution '{solution_id}'")
    return {"id": solution.id, "run_id": solution.run_id, **solution.payload}


@router.get("/solutions/{solution_id}/provenance")
def get_provenance(solution_id: str, session: Session = SessionDep) -> dict:
    """The full chain: source -> fact -> parameter -> constraint -> model ->
    run -> solution -> decision."""
    solution = session.get(m.Solution, solution_id)
    if solution is None:
        raise HTTPException(status_code=404, detail=f"no solution '{solution_id}'")
    explanation = session.scalar(
        select(m.Explanation).where(
            m.Explanation.solution_id == solution.id, m.Explanation.decision_key == "*"
        )
    )
    if explanation is None:
        raise HTTPException(
            status_code=404, detail="no provenance graph was recorded for this solution"
        )
    return {"solution_id": solution.id, **explanation.graph}


@router.get("/solutions/{solution_id}/explain")
def explain(
    solution_id: str,
    decision: str = Query(
        ..., description="A decision variable key, e.g. 'ship[depot_north,base_1]'"
    ),
    session: Session = SessionDep,
) -> dict:
    """Answer 'why is this decision what it is?' from the recorded model.

    The model is recompiled from the problem spec and scenario that the run
    recorded, so the explanation is built against exactly the model that
    produced the solution rather than against the problem as it stands today.
    """
    solution = session.get(m.Solution, solution_id)
    if solution is None:
        raise HTTPException(status_code=404, detail=f"no solution '{solution_id}'")
    run = session.get(m.Run, solution.run_id)
    problem = session.get(m.Problem, run.problem_id)
    version = session.get(m.ModelVersion, run.model_version_id)

    compiled = compile_and_flatten(load_spec(problem), run.scenario_key)
    if version and compiled.ir.fingerprint() != version.fingerprint:
        # The problem has been edited since the run. Explaining the new model
        # against the old numbers would be worse than refusing.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "the problem has changed since this run, so this solution can no longer "
                "be explained against it; re-solve to get a current explanation"
            ),
        )

    parsed = Solution.model_validate(solution.payload)
    try:
        result = explain_decision(compiled, parsed, decision)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    stored = m.Explanation(
        solution_id=solution.id, decision_key=decision, narrative=result.narrative, graph={},
    )
    session.add(stored)
    return result.model_dump(mode="json")


@router.get("/audit")
def audit_trail(
    limit: int = Query(default=100, le=500), session: Session = SessionDep
) -> dict:
    events = session.scalars(
        select(m.AuditEvent).order_by(m.AuditEvent.occurred_at.desc()).limit(limit)
    ).all()
    return {
        "events": [
            {
                "id": e.id, "actor": e.actor, "action": e.action,
                "target_kind": e.target_kind, "target_id": e.target_id,
                "detail": e.detail,
                "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
            }
            for e in events
        ]
    }
