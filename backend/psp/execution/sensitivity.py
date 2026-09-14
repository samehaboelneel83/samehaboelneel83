"""Sensitivity analysis.

Two complementary things are on offer, and the difference matters:

* **Shadow prices** come free with an LP solve and are exact — but only locally,
  and only for continuous models. They answer "what is one more unit worth?"
* **Re-solving** perturbs a parameter and solves again. Expensive, but it is the
  only honest option for integer models, where the objective is a step function
  and a dual does not exist.

The platform prefers the first where it is valid and falls back to the second
where it is not, rather than quoting a dual for a model that has no meaningful
one.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from psp.compiler.pipeline import compile_and_flatten
from psp.execution.runner import run_solver
from psp.problem.spec import ProblemSpec, Scenario, ScenarioOverride
from psp.provenance.solution import build_solution
from psp.solvers.base import SolveOptions


class SensitivityPoint(BaseModel):
    parameter: str
    index: list[str] = Field(default_factory=list)
    multiplier: float
    baseline_value: float
    perturbed_value: float
    objective: float | None = None
    status: str = "optimal"
    delta: float | None = None
    percent_change: float | None = None


class SensitivityReport(BaseModel):
    method: str
    objective_name: str | None = None
    baseline_objective: float | None = None
    points: list[SensitivityPoint] = Field(default_factory=list)
    ranking: list[dict] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def one_at_a_time(
    spec: ProblemSpec,
    parameters: list[str] | None = None,
    multipliers: tuple[float, ...] = (0.9, 1.1),
    scenario_key: str | None = None,
    options: SolveOptions | None = None,
    max_solves: int = 40,
) -> SensitivityReport:
    """Perturb each parameter in turn and re-solve.

    Aggregate parameters are perturbed across every index at once: moving a
    single cell of a cost matrix usually tells you nothing, while moving the
    whole table answers a question a planner actually has.
    """
    options = options or SolveOptions(time_limit_seconds=15.0)
    report = SensitivityReport(method="one_at_a_time")

    baseline = compile_and_flatten(spec, scenario_key)
    base_result, _ = run_solver(baseline.flat, options=options)
    base_solution = build_solution(baseline, base_result)
    if not base_solution.objectives:
        report.notes.append("the baseline run produced no objective value; nothing to compare")
        return report

    primary = base_solution.objectives[0]
    report.objective_name = primary.name
    report.baseline_objective = primary.value

    candidates = parameters or [p.name for p in spec.parameters if p.values]
    budget = max_solves
    for name in candidates:
        param = spec.parameter(name)
        if not param.values:
            continue
        baseline_total = sum(v.value for v in param.values)
        for multiplier in multipliers:
            if budget <= 0:
                report.notes.append(
                    f"stopped after {max_solves} solves; increase max_solves to cover "
                    f"the remaining parameters"
                )
                break
            budget -= 1
            probe = spec.model_copy(deep=True)
            probe.scenarios.append(
                Scenario(
                    key="__sensitivity",
                    name=f"{name} x{multiplier}",
                    overrides=[
                        ScenarioOverride(parameter=name, index=v.index, scale=multiplier)
                        for v in param.values
                    ],
                )
            )
            point = SensitivityPoint(
                parameter=name, multiplier=multiplier,
                baseline_value=baseline_total, perturbed_value=baseline_total * multiplier,
            )
            try:
                compiled = compile_and_flatten(probe, "__sensitivity")
                result, _ = run_solver(compiled.flat, options=options)
                solution = build_solution(compiled, result)
                point.status = solution.status
                if solution.objectives:
                    point.objective = solution.objectives[0].value
                    point.delta = point.objective - primary.value
                    if primary.value:
                        point.percent_change = 100.0 * point.delta / abs(primary.value)
            except Exception as exc:
                # A perturbation that makes the problem inconsistent is itself a
                # finding: report it rather than aborting the sweep.
                point.status = f"error: {type(exc).__name__}: {exc}"
            report.points.append(point)

    influence: dict[str, float] = {}
    for point in report.points:
        if point.delta is not None:
            influence[point.parameter] = max(
                influence.get(point.parameter, 0.0), abs(point.delta)
            )
    report.ranking = [
        {"parameter": name, "max_absolute_change": value}
        for name, value in sorted(influence.items(), key=lambda kv: -kv[1])
    ]
    if not report.ranking:
        report.notes.append("no perturbation changed the objective; the solution is robust "
                            "to these parameters at these magnitudes")
    return report


def shadow_prices(spec: ProblemSpec, scenario_key: str | None = None,
                  options: SolveOptions | None = None) -> SensitivityReport:
    """Exact local sensitivities from LP duality, where the model admits them."""
    options = options or SolveOptions(time_limit_seconds=15.0)
    compiled = compile_and_flatten(spec, scenario_key)
    report = SensitivityReport(method="shadow_prices")

    if compiled.flat.stats()["is_integer"]:
        report.notes.append(
            "this model contains integer variables, so no dual values exist; "
            "use the one-at-a-time method instead"
        )
        return report

    result, _ = run_solver(compiled.flat, solver="highs", options=options)
    solution = build_solution(compiled, result)
    if solution.objectives:
        report.objective_name = solution.objectives[0].name
        report.baseline_objective = solution.objectives[0].value
    report.ranking = [
        {
            "constraint": outcome.key,
            "statement": outcome.statement,
            "marginal_effect": outcome.marginal_effect,
            "objective": outcome.marginal_objective,
        }
        for outcome in solution.binding_constraints
        if outcome.marginal_effect
    ]
    report.ranking.sort(key=lambda r: -abs(r["marginal_effect"]))
    if not report.ranking:
        report.notes.append("no constraint has a non-zero shadow price")
    return report
