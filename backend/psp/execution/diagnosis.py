"""Why there is no plan.

An infeasible model is the one case where a solver says least and the user
needs most. "Infeasible" names the outcome and nothing else: not which rules
are fighting, not by how much, not what would have to give.

The method here is to ask a different question. Every hard row is given room to
be broken — the same room :mod:`psp.compiler.flatten` gives a soft constraint —
and the model is solved twice: once for the smallest *number* of rules that
must give, and again for the smallest *amount* they must give by. What comes
back is a set of rules, a quantity against each, and the numbers behind them.

Two things this deliberately is not. It is not an irreducible infeasible
subsystem: it answers "what would have to change" rather than "which rules are
mutually contradictory", and the first is the question a planner asks. And it
never relaxes anything in the real model — the elastic copy exists for the
length of the diagnosis and is thrown away.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from psp.compiler.flat import FlatConstraint, FlatModel, FlatObjective, FlatVar
from psp.compiler.pipeline import CompiledProblem
from psp.execution.runner import run_solver
from psp.ir.walk import constraint_parameters
from psp.provenance.explain import ParameterEvidence, _parameter_evidence
from psp.provenance.labels import LabelResolver
from psp.solvers.base import SolveOptions, SolveStatus

TOLERANCE = 1e-6


class Conflict(BaseModel):
    """One rule that has to give, and by how much."""

    key: str
    name: str
    statement: str | None = None
    index: list[str] = Field(default_factory=list)
    label: str | None = None
    op: str
    rhs: float
    asks_for: float
    """What the rule's own left-hand side comes to once the others are honoured."""
    shortfall: float
    direction: str
    parameters: list[ParameterEvidence] = Field(default_factory=list)


class Resolution(BaseModel):
    kind: str
    target: str
    description: str | None = None
    amount: float | None = None
    unit: str | None = None
    note: str


class Diagnosis(BaseModel):
    """What stands between this problem and a plan."""

    diagnosed: bool
    method: str
    conflicts: list[Conflict] = Field(default_factory=list)
    resolutions: list[Resolution] = Field(default_factory=list)
    rules_that_must_give: int = 0
    total_shortfall: float = 0.0
    """Summed across the conflicts, in each rule's own units — so it ranks
    repairs within one model and means nothing between two."""
    message: str | None = None
    narrative: list[str] = Field(default_factory=list)


def diagnose(
    compiled: CompiledProblem, options: SolveOptions | None = None
) -> Diagnosis:
    """Find the smallest set of rules whose relaxation admits a plan."""
    options = options or SolveOptions()
    elastic, slack_of, indicator_of = _elasticise(compiled.flat)

    # Pass one: how few rules can we get away with breaking?
    elastic.objective = FlatObjective(
        sense="minimize", terms={key: 1.0 for key in indicator_of.values()}
    )
    counted, _ = run_solver(elastic, options=options)
    if not counted.status.has_solution:
        return Diagnosis(
            diagnosed=False,
            method="elastic relaxation",
            message=(
                "the model is infeasible even with every rule allowed to bend, so "
                "the conflict is in the variable bounds rather than between rules"
            ),
        )
    rules = round(sum(counted.values.get(k, 0.0) for k in indicator_of.values()))

    # Pass two: holding that count, how little can they move?
    elastic.constraints.append(
        FlatConstraint(
            key="~at_most_this_many_rules_give",
            name="~at_most_this_many_rules_give",
            terms={key: 1.0 for key in indicator_of.values()},
            op="le",
            rhs=float(rules),
        )
    )
    elastic.objective = FlatObjective(
        sense="minimize",
        terms={key: 1.0 for keys in slack_of.values() for key in keys},
    )
    moved, _ = run_solver(elastic, options=options)
    answer = moved if moved.status.has_solution else counted

    conflicts = _conflicts(compiled, elastic, slack_of, answer.values)
    return Diagnosis(
        diagnosed=True,
        method="elastic relaxation, fewest rules first",
        conflicts=conflicts,
        resolutions=_resolutions(conflicts),
        rules_that_must_give=len(conflicts),
        total_shortfall=sum(c.shortfall for c in conflicts),
        narrative=_narrate(conflicts),
    )


def _elasticise(
    flat: FlatModel,
) -> tuple[FlatModel, dict[str, list[str]], dict[str, str]]:
    """A copy of the model in which every rule can bend, and is counted when it does.

    The indicator is what makes "fewest rules" askable at all. Its coefficient
    is the row's own reach — the most its left-hand side can move given the
    columns it is built from — so the constant is read off the model rather
    than invented, which is the condition this platform puts on any big-M.
    """
    elastic = flat.model_copy(deep=True)
    bounds = {v.key: v for v in elastic.variables}
    slack_of: dict[str, list[str]] = {}
    indicator_of: dict[str, str] = {}

    # Snapshot first: the loop appends the counting rows below, and a counting
    # row that got elastic room of its own would let the count be cheated.
    for row in list(elastic.constraints):
        if row.violation_keys:
            continue  # already soft: it bends by design and is not a conflict
        reach = _reach(row, bounds)
        keys: list[str] = []
        for role, coefficient in _directions(row.op):
            key = f"~give[{role}][{row.key}]"
            cap = _cap(role, reach, row.rhs)
            elastic.variables.append(
                FlatVar(key=key, name=f"~give_{role}", index=[row.key],
                        kind="continuous", lb=0.0, ub=cap, role="violation")
            )
            row.terms[key] = coefficient
            keys.append(key)
        slack_of[row.key] = keys

        cap = max((v for v in (_cap(r, reach, row.rhs) for r, _ in _directions(row.op))
                   if v is not None), default=None)
        if cap and cap > 0:
            flag = f"~gave[{row.key}]"
            elastic.variables.append(
                FlatVar(key=flag, name="~gave", index=[row.key],
                        kind="binary", lb=0.0, ub=1.0, role="violation")
            )
            indicator_of[row.key] = flag
            for key in keys:
                elastic.constraints.append(
                    FlatConstraint(
                        key=f"~counts[{key}]", name="~counts",
                        terms={key: 1.0, flag: -cap}, op="le", rhs=0.0,
                    )
                )
    return elastic, slack_of, indicator_of


def _directions(op: str) -> list[tuple[str, float]]:
    if op == "le":
        return [("over", -1.0)]
    if op == "ge":
        return [("under", 1.0)]
    return [("under", 1.0), ("over", -1.0)]


def _reach(row: FlatConstraint, bounds: dict[str, FlatVar]) -> tuple[float | None, float | None]:
    lo = hi = 0.0
    for key, coef in row.terms.items():
        column = bounds.get(key)
        if column is None or column.ub is None:
            return None, None
        low, high = coef * column.lb, coef * column.ub
        lo += min(low, high)
        hi += max(low, high)
    return lo, hi


def _cap(role: str, reach: tuple[float | None, float | None], rhs: float) -> float | None:
    lo, hi = reach
    if role == "over":
        return None if hi is None else max(0.0, hi - rhs)
    return None if lo is None else max(0.0, rhs - lo)


def _conflicts(
    compiled: CompiledProblem,
    elastic: FlatModel,
    slack_of: dict[str, list[str]],
    values: dict[str, float],
) -> list[Conflict]:
    labels = LabelResolver(compiled.ir)
    by_name = {c.name: c for c in compiled.spec.constraints}
    parameters_by_name = {p.name: p for p in compiled.spec.parameters}
    rows = {c.key: c for c in elastic.constraints}

    out: list[Conflict] = []
    for key, slack_keys in slack_of.items():
        for slack in slack_keys:
            amount = values.get(slack, 0.0)
            if amount <= TOLERANCE:
                continue
            row = rows[key]
            stated = sum(
                coef * values.get(column, 0.0)
                for column, coef in row.terms.items()
                if not column.startswith("~")
            )
            spec_constraint = by_name.get(row.name)
            out.append(
                Conflict(
                    key=row.key,
                    name=row.name,
                    statement=row.statement,
                    index=list(row.index),
                    label=labels.constraint(row.name, row.index),
                    op=row.op,
                    rhs=row.rhs,
                    asks_for=stated,
                    shortfall=amount,
                    direction="under" if "[under]" in slack else "over",
                    parameters=[
                        _parameter_evidence(parameters_by_name.get(name), name)
                        for name in sorted(
                            constraint_parameters(spec_constraint)
                            if spec_constraint is not None
                            else []
                        )
                    ],
                )
            )
    out.sort(key=lambda c: (-c.shortfall, c.key))
    return out


def _resolutions(conflicts: list[Conflict]) -> list[Resolution]:
    """What could be done, largest obstacle first.

    Each conflict yields one resolution that is exact — relax this rule by this
    much — and one per parameter behind it, which names where the number came
    from without claiming to know which way to move it. Naming a parameter and
    an amount would only be honest where the rule's right-hand side is that
    parameter alone, and most are not.
    """
    out: list[Resolution] = []
    for conflict in conflicts:
        out.append(
            Resolution(
                kind="relax_rule",
                target=conflict.label or conflict.key,
                description=conflict.statement,
                amount=conflict.shortfall,
                note=(
                    f"allow this rule to be missed by {conflict.shortfall:g}, or make "
                    f"it soft and price it"
                ),
            )
        )
        for parameter in conflict.parameters:
            out.append(
                Resolution(
                    kind="change_parameter",
                    target=parameter.parameter,
                    description=parameter.description,
                    unit=parameter.unit,
                    note=(
                        f"one of the numbers '{conflict.name}' is built from"
                        + (f"; sourced from {', '.join(parameter.sources)}"
                           if parameter.sources else "")
                    ),
                )
            )
    return out


def _narrate(conflicts: list[Conflict]) -> list[str]:
    if not conflicts:
        return ["The model is infeasible, but no single rule had to give."]
    lines = [
        f"No plan satisfies every rule. {len(conflicts)} would have to give.",
    ]
    for conflict in conflicts:
        subject = conflict.label or conflict.key
        lines.append(
            f"{subject}: asks for {conflict.asks_for:g} against a limit of "
            f"{conflict.rhs:g}, so it is {conflict.direction} by {conflict.shortfall:g}."
        )
        if conflict.statement:
            lines.append(f"  The rule: {conflict.statement}")
    return lines
