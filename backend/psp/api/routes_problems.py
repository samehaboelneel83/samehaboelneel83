"""Problems: create from a template, inspect, compile, solve, compare."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from psp.api.deps import ProblemDep, SessionDep, build_options
from psp.api.schemas import (
    CompareRequest,
    InstantiateRequest,
    ProblemSummary,
    SaveProblemRequest,
    SensitivityRequest,
    SolveRequest,
)
from psp.compiler.errors import CompileError
from psp.compiler.pipeline import compile_and_flatten
from psp.core.security import Principal, current_principal
from psp.db import models as m
from psp.execution import sensitivity as sensitivity_module
from psp.execution.service import load_spec, persist_problem, solve_problem
from psp.problem.templates.registry import get as get_template
from psp.solvers.registry import UnsupportedModelError

router = APIRouter(prefix="/problems", tags=["problems"])


@router.get("", response_model=list[ProblemSummary])
def list_problems(session: Session = SessionDep) -> list[ProblemSummary]:
    problems = session.scalars(select(m.Problem).order_by(m.Problem.updated_at.desc())).all()
    summaries = []
    for problem in problems:
        spec = load_spec(problem)
        summaries.append(
            ProblemSummary(
                id=problem.id, key=problem.key, name=problem.name,
                description=problem.description, template_key=problem.template_key,
                status=problem.status,
                variables=len(spec.variables), constraints=len(spec.constraints),
                scenarios=[s.key for s in spec.scenarios],
                updated_at=problem.updated_at.isoformat() if problem.updated_at else None,
            )
        )
    return summaries


@router.post("", status_code=status.HTTP_201_CREATED)
def instantiate(
    request: InstantiateRequest,
    session: Session = SessionDep,
    principal: Principal = Depends(current_principal),
) -> dict:
    """Build a Problem Model from a template and its data."""
    try:
        template = get_template(request.template)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    try:
        spec = template.build(request.data or template.example())
    except (ValueError, KeyError) as exc:
        # A template refusing its data is a user error with a useful message,
        # not a server fault.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    if request.save:
        problem = persist_problem(session, spec, created_by=None)
        return {
            "key": problem.key, "id": problem.id, "saved": True,
            "spec": spec.model_dump(mode="json"),
        }
    return {"key": spec.key, "saved": False, "spec": spec.model_dump(mode="json")}


@router.put("/{key}")
def save_problem(
    key: str, request: SaveProblemRequest, session: Session = SessionDep
) -> dict:
    """Replace a problem with an edited Problem Model."""
    if request.spec.key != key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"spec key '{request.spec.key}' does not match the path key '{key}'",
        )
    problem = persist_problem(session, request.spec)
    return {"key": problem.key, "id": problem.id, "saved": True}


@router.get("/{key}")
def get_problem_detail(problem: m.Problem = ProblemDep) -> dict:
    return {
        "id": problem.id,
        "key": problem.key,
        "name": problem.name,
        "description": problem.description,
        "template_key": problem.template_key,
        "status": problem.status,
        "spec": problem.spec,
    }


@router.delete("/{key}", status_code=status.HTTP_204_NO_CONTENT)
def delete_problem(problem: m.Problem = ProblemDep, session: Session = SessionDep) -> None:
    session.delete(problem)


@router.post("/{key}/compile")
def compile_problem_endpoint(
    scenario: str | None = None, problem: m.Problem = ProblemDep
) -> dict:
    """Compile without solving, so a model can be inspected before it is run."""
    spec = load_spec(problem)
    try:
        compiled = compile_and_flatten(spec, scenario)
    except CompileError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": str(exc), "where": exc.where},
        ) from exc
    return {
        "fingerprint": compiled.ir.fingerprint(),
        "scenario": scenario,
        "statistics": compiled.flat.stats(),
        "structure": compiled.flat.structure.get("kind"),
        "ir": compiled.ir.model_dump(mode="json"),
        "record": compiled.record.to_dict(),
        "constraint_rows": [
            {"key": c.key, "name": c.name, "statement": c.statement,
             "op": c.op, "rhs": c.rhs, "terms": len(c.terms)}
            for c in compiled.flat.constraints[:500]
        ],
    }


@router.post("/{key}/solve")
def solve(
    request: SolveRequest,
    problem: m.Problem = ProblemDep,
    session: Session = SessionDep,
    principal: Principal = Depends(current_principal),
) -> dict:
    options = build_options(
        request.time_limit_seconds, request.relative_gap, request.threads, request.seed
    )
    try:
        outcome = solve_problem(
            session, problem, scenario_key=request.scenario, solver=request.solver,
            options=options, requested_by=None,
        )
    except CompileError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": str(exc), "where": exc.where},
        ) from exc
    except UnsupportedModelError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": str(exc), "considered": exc.considered},
        ) from exc
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc).strip("'")
        ) from exc

    return {
        "run_id": outcome["run_id"],
        "solution_id": outcome["solution_id"],
        "model_version_id": outcome["model_version_id"],
        "fingerprint": outcome["fingerprint"],
        "statistics": outcome["statistics"],
        "selection": outcome["selection"],
        "solution": outcome["solution"].model_dump(mode="json"),
    }


@router.post("/{key}/compare")
def compare_scenarios(
    request: CompareRequest,
    problem: m.Problem = ProblemDep,
    session: Session = SessionDep,
) -> dict:
    """Solve the same problem under several scenarios and line the answers up.

    Comparing scenarios is the point of having them, and doing it in one call
    means every row shares a solver, a time limit and a code path — so a
    difference between columns is a difference in the data, not in the run.
    """
    options = build_options(request.time_limit_seconds)
    spec = load_spec(problem)
    known = {s.key for s in spec.scenarios}
    results = []
    for scenario in request.scenarios:
        if scenario is not None and scenario not in known:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"problem '{problem.key}' has no scenario '{scenario}'",
            )
        try:
            outcome = solve_problem(
                session, problem, scenario_key=scenario, solver=request.solver, options=options
            )
            solution = outcome["solution"]
            results.append({
                "scenario": scenario or "baseline",
                "status": solution.status,
                "solver": solution.solver,
                "objectives": [o.model_dump(mode="json") for o in solution.objectives],
                "run_id": outcome["run_id"],
                "solution_id": outcome["solution_id"],
                "decisions": len(solution.decisions),
                "binding_constraints": len(solution.binding_constraints),
                "warnings": solution.warnings,
            })
        except CompileError as exc:
            results.append({
                "scenario": scenario or "baseline", "status": "compile_error",
                "error": str(exc), "objectives": [],
            })

    baseline = next(
        (r for r in results if r["scenario"] == "baseline"),
        results[0] if results else None,
    )
    if baseline and baseline.get("objectives"):
        reference = baseline["objectives"][0]["value"]
        for row in results:
            if row.get("objectives"):
                row["delta_vs_baseline"] = row["objectives"][0]["value"] - reference
    return {"problem": problem.key, "comparison": results}


@router.post("/{key}/sensitivity")
def sensitivity(
    request: SensitivityRequest,
    problem: m.Problem = ProblemDep,
    session: Session = SessionDep,
) -> dict:
    spec = load_spec(problem)
    options = build_options(request.time_limit_seconds or 15.0)
    if request.method == "shadow_prices":
        report = sensitivity_module.shadow_prices(spec, request.scenario, options)
    else:
        report = sensitivity_module.one_at_a_time(
            spec,
            parameters=request.parameters,
            multipliers=tuple(request.multipliers),
            scenario_key=request.scenario,
            options=options,
            max_solves=request.max_solves,
        )
    record = m.SensitivityAnalysis(
        problem_id=problem.id, method=report.method,
        results=[p.model_dump(mode="json") for p in report.points],
    )
    session.add(record)
    return {"analysis_id": record.id, **report.model_dump(mode="json")}
