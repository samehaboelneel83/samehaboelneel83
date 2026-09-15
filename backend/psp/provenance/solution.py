"""Turning a raw solver answer into a solution people can act on and check.

A solver returns a vector of numbers. That is not a recommendation. This module
reconstitutes the structure the compiler flattened away: what each objective is
actually worth in its own units, which decisions were taken, which constraints
turned out to be the ones that bit, and how much slack is left everywhere else.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from psp.compiler.pipeline import CompiledProblem
from psp.provenance.labels import LabelResolver
from psp.solvers.base import SolveResult

TOLERANCE = 1e-6


class ObjectiveValue(BaseModel):
    name: str
    sense: str
    value: float
    weight: float
    unit: str | None = None
    statement: str | None = None


class Decision(BaseModel):
    """One non-trivial choice the solver made."""

    variable: str
    index: list[str] = Field(default_factory=list)
    key: str
    value: float
    meaning: str | None = None
    label: str | None = None
    """The key with its indices rendered for a reader, when any set declares
    labels. ``key`` stays the identifier; this is only for display."""


class ConstraintOutcome(BaseModel):
    key: str
    name: str
    statement: str | None = None
    index: list[str] = Field(default_factory=list)
    label: str | None = None
    activity: float
    op: str
    rhs: float
    slack: float
    binding: bool
    shadow_price: float | None = None
    """Raw dual of the internal minimisation. Sign conventions differ between
    engines and senses, so prefer :attr:`marginal_effect` when reporting."""
    marginal_effect: float | None = None
    """Change in :attr:`marginal_objective` per unit increase in ``rhs``,
    expressed in that objective's own direction and units. Only available for
    single-objective linear models, where a dual is unambiguous."""
    marginal_objective: str | None = None


class Solution(BaseModel):
    status: str
    solver: str
    objectives: list[ObjectiveValue] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    binding_constraints: list[ConstraintOutcome] = Field(default_factory=list)
    slack_constraints: list[ConstraintOutcome] = Field(default_factory=list)
    values: dict[str, float] = Field(default_factory=dict)
    duals_available: bool = False
    duals_unavailable_reason: str | None = None
    """Why no shadow prices came back, and what to do about it. Absent duals
    have several quite different causes — an integer model has none to give, a
    combinatorial engine does not compute them — and telling them apart is the
    difference between 'try another engine' and 'use sensitivity analysis'."""
    wall_time_seconds: float = 0.0
    gap: float | None = None
    message: str | None = None
    warnings: list[str] = Field(default_factory=list)
    diagnostics: dict = Field(default_factory=dict)


def build_solution(compiled: CompiledProblem, result: SolveResult) -> Solution:
    """Interpret ``result`` in terms of the problem that produced it."""
    solution = Solution(
        status=result.status.value,
        solver=result.solver,
        values=result.values,
        wall_time_seconds=result.wall_time_seconds,
        gap=result.gap,
        message=result.message,
        warnings=list(compiled.record.warnings),
        diagnostics=result.diagnostics,
    )
    if not result.status.has_solution or not result.values:
        return solution

    solution.duals_available = bool(result.duals)
    if not result.duals:
        solution.duals_unavailable_reason = _why_no_duals(compiled, result)

    solution.objectives = _objectives(compiled, result.values)
    solution.decisions = _decisions(compiled, result.values)
    binding, slack = _constraints(compiled, result)
    solution.binding_constraints = binding
    solution.slack_constraints = slack
    return solution


def _why_no_duals(compiled: CompiledProblem, result: SolveResult) -> str:
    """Name the actual cause rather than guessing at the most common one."""
    from psp.solvers.registry import capabilities

    if compiled.flat.stats()["is_integer"]:
        return (
            "this model has integer variables, so no dual values exist; use "
            "sensitivity analysis to see what a parameter is worth"
        )
    try:
        engine_provides_duals = capabilities(result.solver).duals
    except KeyError:
        engine_provides_duals = False
    if not engine_provides_duals:
        return (
            f"the '{result.solver}' engine does not compute dual values; "
            "re-solve with 'highs' to get shadow prices for this model"
        )
    if len(compiled.flat.objective.components) > 1:
        return (
            "this model has several weighted objectives, so a dual refers to the "
            "composite rather than to any one of them"
        )
    return "the solver returned no dual values for this model"


def _objectives(compiled: CompiledProblem, values: dict[str, float]) -> list[ObjectiveValue]:
    """Report each objective in its own units and its own direction.

    The flat model minimises a single weighted sum with maximisation negated;
    reporting that number to a user would be actively misleading, so each
    component is re-evaluated here from the solution vector.
    """
    statements = {o.name: o.statement for o in compiled.spec.objectives}
    out: list[ObjectiveValue] = []
    for component in compiled.flat.objective.components:
        if component["name"] == "feasibility":
            continue
        value = component.get("constant", 0.0) + sum(
            coef * values.get(key, 0.0) for key, coef in component["terms"].items()
        )
        # A quadratic objective is reported at its full value, not at its linear
        # part — the number on the screen has to be the thing that was minimised.
        value += sum(
            coef * values.get(i, 0.0) * values.get(j, 0.0)
            for i, j, coef in component.get("quadratic", ())
        )
        out.append(
            ObjectiveValue(
                name=component["name"],
                sense=component["sense"],
                value=value,
                weight=component.get("weight", 1.0),
                unit=component.get("unit"),
                statement=statements.get(component["name"]),
            )
        )
    return out


def _decisions(compiled: CompiledProblem, values: dict[str, float]) -> list[Decision]:
    meanings = {
        v.name: v.decision_meaning or v.description for v in compiled.spec.variables
    }
    labels = LabelResolver(compiled.ir)
    out: list[Decision] = []
    for var in compiled.flat.variables:
        value = values.get(var.key, 0.0)
        if abs(value) <= TOLERANCE:
            continue  # a decision not taken is not a decision
        out.append(
            Decision(
                variable=var.name, index=var.index, key=var.key,
                value=value, meaning=meanings.get(var.name),
                label=labels.variable(var.name, var.index),
            )
        )
    out.sort(key=lambda d: (d.variable, d.index))
    return out


def _constraints(
    compiled: CompiledProblem, result: SolveResult
) -> tuple[list[ConstraintOutcome], list[ConstraintOutcome]]:
    # A dual only means something against a single objective. The flat model
    # minimises a weighted composite with maximisation negated, so the raw dual
    # is converted back into "effect on the objective the user actually stated".
    components = [c for c in compiled.flat.objective.components if c["name"] != "feasibility"]
    reported: str | None = None
    scale: float | None = None
    if len(components) == 1 and components[0].get("weight"):
        reported = components[0]["name"]
        direction = 1.0 if components[0]["sense"] == "minimize" else -1.0
        scale = direction * float(components[0]["weight"])

    labels = LabelResolver(compiled.ir)
    binding: list[ConstraintOutcome] = []
    slack_rows: list[ConstraintOutcome] = []
    for c in compiled.flat.constraints:
        activity = sum(coef * result.values.get(key, 0.0) for key, coef in c.terms.items())
        dual = result.duals.get(c.key)
        if c.op == "le":
            slack = c.rhs - activity
        elif c.op == "ge":
            slack = activity - c.rhs
        else:
            slack = abs(activity - c.rhs)
        outcome = ConstraintOutcome(
            key=c.key, name=c.name, statement=c.statement, index=c.index,
            label=labels.constraint(c.name, c.index),
            activity=activity, op=c.op, rhs=c.rhs, slack=slack,
            binding=abs(slack) <= max(TOLERANCE, abs(c.rhs) * 1e-9),
            shadow_price=dual,
            marginal_effect=(dual / scale if dual is not None and scale else None),
            marginal_objective=(reported if dual is not None and scale else None),
        )
        (binding if outcome.binding else slack_rows).append(outcome)
    # Equalities are binding by construction and would drown out the rows that
    # actually explain the answer, so the tightest inequalities come first.
    binding.sort(key=lambda o: (o.op == "eq", o.name, o.index))
    slack_rows.sort(key=lambda o: o.slack)
    return binding, slack_rows
