"""Orchestration: from a stored problem to a stored, explainable solution.

Everything this module writes is written for a reader who was not present when
the run happened — the compiled IR, the mapping back to the problem, which
solvers were eligible, the options used, and the solution. A run that cannot be
reconstructed from its own rows has not really been recorded.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from psp.compiler.pipeline import CompiledProblem, compile_and_flatten
from psp.db import models as m
from psp.execution.runner import run_solver
from psp.problem.spec import ProblemSpec
from psp.provenance.chain import build_graph
from psp.provenance.solution import Solution, build_solution
from psp.solvers.base import SolveOptions, SolveResult


def load_spec(problem: m.Problem) -> ProblemSpec:
    return ProblemSpec.model_validate(problem.spec)


def persist_problem(
    session: Session, spec: ProblemSpec, created_by: str | None = None
) -> m.Problem:
    """Store a problem, replacing any previous revision of the same key.

    The JSON ``spec`` is authoritative; the child rows are a queryable
    projection of it, rebuilt together so the two can never disagree.
    """
    problem = session.scalar(select(m.Problem).where(m.Problem.key == spec.key))
    if problem is None:
        problem = m.Problem(key=spec.key, created_by=created_by)
        session.add(problem)
    problem.name = spec.name
    problem.description = spec.description
    problem.template_key = spec.template_key
    problem.spec = spec.model_dump(mode="json")
    session.flush()

    for table in (m.Parameter, m.Variable, m.Constraint, m.Objective,
                  m.Assumption, m.Scenario, m.Uncertainty):
        for row in session.scalars(select(table).where(table.problem_id == problem.id)):
            session.delete(row)
    session.flush()

    for p in spec.parameters:
        session.add(m.Parameter(
            problem_id=problem.id, name=p.name, index_sets=p.index_sets, unit=p.unit,
            description=p.description,
            values=[v.model_dump(mode="json") for v in p.values],
        ))
    for v in spec.variables:
        session.add(m.Variable(
            problem_id=problem.id, name=v.name, index_sets=v.index_sets, kind=v.kind,
            lower_bound=v.lb, upper_bound=v.ub, decision_meaning=v.decision_meaning,
        ))
    for c in spec.constraints:
        session.add(m.Constraint(
            problem_id=problem.id, name=c.name, statement=c.statement, category=c.category,
            rationale=c.rationale, expression=c.rel.model_dump(mode="json"),
        ))
    for o in spec.objectives:
        session.add(m.Objective(
            problem_id=problem.id, name=o.name, statement=o.statement, sense=o.sense,
            weight=o.weight, unit=o.unit, expression=o.expr.model_dump(mode="json"),
        ))
    for a in spec.assumptions:
        session.add(m.Assumption(
            problem_id=problem.id, key=a.key, statement=a.statement,
            rationale=a.rationale, affects=a.affects,
        ))
    for s in spec.scenarios:
        session.add(m.Scenario(
            problem_id=problem.id, key=s.key, name=s.name, description=s.description,
            overrides=[o.model_dump(mode="json") for o in s.overrides],
        ))
    for u in spec.uncertainty:
        session.add(m.Uncertainty(
            problem_id=problem.id, parameter_name=u.parameter, index=u.index,
            distribution=u.distribution, relative=u.relative,
            low=u.low, high=u.high, mode=u.mode,
        ))

    audit(session, "problem.saved", "problem", problem.id,
          {"key": spec.key, "template": spec.template_key}, actor=created_by)
    session.flush()
    return problem


def persist_model_version(
    session: Session, problem: m.Problem, compiled: CompiledProblem, considered: list[dict]
) -> m.ModelVersion:
    """Store the compiled model. Identical compilations reuse their version."""
    computational = session.scalar(
        select(m.ComputationalModel).where(m.ComputationalModel.problem_id == problem.id)
    )
    if computational is None:
        computational = m.ComputationalModel(
            problem_id=problem.id, name=f"{problem.name} model",
            description="Compiled from the problem model",
        )
        session.add(computational)
        session.flush()

    fingerprint = compiled.ir.fingerprint()
    existing = session.scalar(
        select(m.ModelVersion).where(
            m.ModelVersion.computational_model_id == computational.id,
            m.ModelVersion.fingerprint == fingerprint,
            m.ModelVersion.scenario_key.is_(compiled.record.scenario_key),
        )
    )
    if existing is not None:
        # The same problem and scenario compiled to the same model; reusing the
        # version keeps runs comparable instead of multiplying near-identical rows.
        return existing

    highest = session.scalar(
        select(m.ModelVersion.version)
        .where(m.ModelVersion.computational_model_id == computational.id)
        .order_by(m.ModelVersion.version.desc())
    )
    version = m.ModelVersion(
        computational_model_id=computational.id,
        version=(highest or 0) + 1,
        scenario_key=compiled.record.scenario_key,
        fingerprint=fingerprint,
        ir=compiled.ir.model_dump(mode="json"),
        compilation_record=compiled.record.to_dict(),
        statistics=compiled.flat.stats(),
    )
    session.add(version)
    session.flush()

    for var in compiled.ir.variables:
        session.add(m.ModelVariable(
            model_version_id=version.id, name=var.name, index_sets=var.index_sets,
            kind=var.kind,
            column_count=sum(1 for c in compiled.flat.variables if c.name == var.name),
        ))
    for constraint in compiled.ir.constraints:
        session.add(m.ModelConstraint(
            model_version_id=version.id, name=constraint.name, statement=constraint.statement,
            row_count=sum(1 for r in compiled.flat.constraints if r.name == constraint.name),
        ))
    for objective in compiled.ir.objectives:
        session.add(m.ModelObjective(
            model_version_id=version.id, name=objective.name, sense=objective.sense,
            weight=objective.weight, unit=objective.unit,
        ))
    for mapping in compiled.record.mappings:
        session.add(m.ModelMapping(model_version_id=version.id, **mapping.__dict__))
    for entry in considered:
        session.add(m.ModelSolver(
            model_version_id=version.id, solver_key=entry["solver"],
            eligible=entry["eligible"], reason=entry.get("reason"),
        ))
    flat = compiled.flat.model_dump(mode="json")
    session.add(m.ModelArtifact(
        model_version_id=version.id, kind="flat_model", content=flat,
        byte_size=len(json.dumps(flat)),
    ))
    session.flush()
    return version


def persist_run(
    session: Session,
    problem: m.Problem,
    version: m.ModelVersion,
    compiled: CompiledProblem,
    result: SolveResult,
    solution: Solution,
    trace: dict,
    options: SolveOptions,
    requested_by: str | None = None,
) -> tuple[m.Run, m.Solution | None]:
    run = m.Run(
        model_version_id=version.id,
        problem_id=problem.id,
        scenario_key=compiled.record.scenario_key,
        solver_key=result.solver,
        status=result.status.value,
        finished_at=datetime.now(UTC),
        wall_time_seconds=result.wall_time_seconds,
        message=result.message,
        selection_trace=trace,
        requested_by=requested_by,
    )
    session.add(run)
    session.flush()

    for key, value in options.model_dump(mode="json").items():
        session.add(m.RunParameter(run_id=run.id, key=key, value=json.dumps(value)))
    for index, line in enumerate(result.log):
        session.add(m.RunLog(run_id=run.id, sequence=index, message=line))
    for key, value in result.diagnostics.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            session.add(m.RunMetric(run_id=run.id, key=key, value=float(value)))

    if not result.status.has_solution:
        audit(session, "run.failed", "run", run.id,
              {"status": result.status.value, "message": result.message}, actor=requested_by)
        session.flush()
        return run, None

    stored = m.Solution(
        run_id=run.id,
        status=solution.status,
        objective_values=[o.model_dump(mode="json") for o in solution.objectives],
        gap=solution.gap,
        payload=solution.model_dump(mode="json"),
    )
    session.add(stored)
    session.flush()

    for var in compiled.flat.variables:
        value = solution.values.get(var.key, 0.0)
        session.add(m.SolutionVariable(
            solution_id=stored.id, variable_name=var.name, index=var.index,
            key=var.key, value=value,
        ))
    for decision in solution.decisions:
        session.add(m.Decision(
            solution_id=stored.id, key=decision.key,
            description=decision.meaning, value=decision.value,
        ))
    for outcome in solution.binding_constraints[:50]:
        evidence = m.Evidence(
            kind="binding_constraint",
            summary=outcome.statement or outcome.name,
            detail=outcome.model_dump(mode="json"),
        )
        session.add(evidence)
        session.flush()
        session.add(m.EvidenceLink(
            evidence_id=evidence.id, target_kind="solution",
            target_id=stored.id, relation="explains",
        ))

    graph = build_graph(compiled, solution, run.id)
    session.add(m.Explanation(
        solution_id=stored.id, decision_key="*",
        narrative=[f"Solved by {result.solver}; {len(solution.decisions)} decisions recommended."],
        graph=graph.model_dump(mode="json"),
    ))
    session.add(m.Transformation(
        key="compile_and_solve", name="Problem compiled and solved", kind="compile",
        inputs=[problem.id], outputs=[stored.id],
        detail={"fingerprint": version.fingerprint, "solver": result.solver},
    ))
    audit(session, "run.completed", "run", run.id,
          {"solver": result.solver, "status": solution.status,
           "objectives": [o.model_dump(mode="json") for o in solution.objectives]},
          actor=requested_by)
    session.flush()
    return run, stored


def solve_problem(
    session: Session,
    problem: m.Problem,
    scenario_key: str | None = None,
    solver: str | None = None,
    options: SolveOptions | None = None,
    requested_by: str | None = None,
) -> dict:
    """Compile, solve, interpret and record. The single path every run takes."""
    options = options or SolveOptions()
    spec = load_spec(problem)
    compiled = compile_and_flatten(spec, scenario_key)
    result, trace = run_solver(compiled.flat, solver=solver, options=options)
    solution = build_solution(compiled, result)
    version = persist_model_version(session, problem, compiled, trace["considered"])
    run, stored = persist_run(
        session, problem, version, compiled, result, solution, trace, options, requested_by
    )
    return {
        "run_id": run.id,
        "solution_id": stored.id if stored else None,
        "model_version_id": version.id,
        "fingerprint": version.fingerprint,
        "statistics": compiled.flat.stats(),
        "selection": trace,
        "solution": solution,
        "compiled": compiled,
    }


def audit(
    session: Session, action: str, target_kind: str, target_id: str,
    detail: dict, actor: str | None = None,
) -> None:
    session.add(m.AuditEvent(
        actor=actor, action=action, target_kind=target_kind,
        target_id=target_id, detail=detail,
    ))
