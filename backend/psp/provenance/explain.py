"""Explaining a single decision.

Answers "why is this variable at this value?" from the compiled model and the
solution vector alone. The answer is assembled from facts the platform already
holds — the constraints the variable appears in, which of them are tight, what
each is worth at the margin, and which parameters (and therefore which sources)
feed them.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from psp.compiler.pipeline import CompiledProblem
from psp.ir.walk import constraint_parameters
from psp.provenance.labels import LabelResolver
from psp.provenance.solution import Solution


class ParameterEvidence(BaseModel):
    parameter: str
    description: str | None = None
    unit: str | None = None
    sources: list[str] = Field(default_factory=list)


class ConstraintEvidence(BaseModel):
    key: str
    name: str
    label: str | None = None
    statement: str | None = None
    category: str | None = None
    rationale: str | None = None
    coefficient: float | None = None
    alternatives_removed: int | None = None
    """How many other placements of the same subject this row forbids. Set only
    on ``ruled_out`` evidence, where the row never touches the chosen one."""
    binding: bool
    slack: float
    shadow_price: float | None = None
    marginal_effect: float | None = None
    marginal_objective: str | None = None
    parameters: list[ParameterEvidence] = Field(default_factory=list)


class Explanation(BaseModel):
    decision: str
    label: str | None = None
    value: float
    meaning: str | None = None
    objective_contribution: dict[str, float] = Field(default_factory=dict)
    limited_by: list[ConstraintEvidence] = Field(default_factory=list)
    also_appears_in: list[ConstraintEvidence] = Field(default_factory=list)
    ruled_out: list[ConstraintEvidence] = Field(default_factory=list)
    """Why the alternatives went away.

    A rule that forbids something never mentions what was chosen, so it can
    never appear in ``limited_by`` — yet "the lecture is not on Sunday because
    of the spring holiday" is usually the question being asked. These are the
    rows that zero out other placements of the same subject."""
    assumptions: list[dict] = Field(default_factory=list)
    scenario: str | None = None
    narrative: list[str] = Field(default_factory=list)


def explain_decision(
    compiled: CompiledProblem, solution: Solution, variable_key: str
) -> Explanation:
    """Explain one decision variable's value."""
    flat = compiled.flat
    if variable_key not in flat.var_index:
        raise KeyError(f"'{variable_key}' is not a variable of this model")

    value = solution.values.get(variable_key, 0.0)
    var = flat.var_index[variable_key]
    labels = LabelResolver(compiled.ir)
    meaning = next(
        (v.decision_meaning or v.description
         for v in compiled.spec.variables if v.name == var.name),
        None,
    )

    constraints_by_name = {c.name: c for c in compiled.spec.constraints}
    parameters_by_name = {p.name: p for p in compiled.spec.parameters}
    outcomes = {
        o.key: o for o in (*solution.binding_constraints, *solution.slack_constraints)
    }

    limited_by: list[ConstraintEvidence] = []
    others: list[ConstraintEvidence] = []
    for row in flat.constraints:
        coefficient = row.terms.get(variable_key)
        if coefficient is None:
            continue
        outcome = outcomes.get(row.key)
        spec_constraint = constraints_by_name.get(row.name)
        evidence = ConstraintEvidence(
            key=row.key,
            name=row.name,
            label=labels.constraint(row.name, row.index),
            statement=row.statement,
            category=spec_constraint.category if spec_constraint else None,
            rationale=spec_constraint.rationale if spec_constraint else None,
            coefficient=coefficient,
            binding=bool(outcome and outcome.binding),
            slack=outcome.slack if outcome else 0.0,
            shadow_price=outcome.shadow_price if outcome else None,
            marginal_effect=outcome.marginal_effect if outcome else None,
            marginal_objective=outcome.marginal_objective if outcome else None,
            parameters=[
                _parameter_evidence(parameters_by_name.get(name), name)
                for name in sorted(constraint_parameters(spec_constraint))
            ] if spec_constraint else [],
        )
        (limited_by if evidence.binding else others).append(evidence)

    # Rules that forbid alternatives: rows fixed at zero which never touch the
    # chosen placement but remove others of the same subject. The subject is
    # taken as the first index — the offering, the activity, the vehicle — which
    # is the convention every template here follows.
    ruled_out: list[ConstraintEvidence] = []
    if var.index:
        subject = var.index[0]
        for row in flat.constraints:
            if row.op != "eq" or abs(row.rhs) > 1e-9 or variable_key in row.terms:
                continue
            removed = sum(
                1
                for key in row.terms
                if (sibling := flat.var_index.get(key)) is not None
                and sibling.name == var.name
                and sibling.index[:1] == [subject]
            )
            if not removed:
                continue
            spec_constraint = constraints_by_name.get(row.name)
            ruled_out.append(
                ConstraintEvidence(
                    key=row.key,
                    name=row.name,
                    label=labels.constraint(row.name, row.index),
                    statement=row.statement,
                    category=spec_constraint.category if spec_constraint else None,
                    rationale=spec_constraint.rationale if spec_constraint else None,
                    alternatives_removed=removed,
                    binding=True,
                    slack=0.0,
                    parameters=[
                        _parameter_evidence(parameters_by_name.get(name), name)
                        for name in sorted(constraint_parameters(spec_constraint))
                    ] if spec_constraint else [],
                )
            )
        ruled_out.sort(key=lambda e: -(e.alternatives_removed or 0))

    contribution = {}
    for component in flat.objective.components:
        coefficient = component["terms"].get(variable_key)
        if coefficient:
            contribution[component["name"]] = coefficient * value

    explanation = Explanation(
        decision=variable_key,
        label=labels.variable(var.name, var.index),
        value=value,
        meaning=meaning,
        objective_contribution=contribution,
        limited_by=sorted(limited_by, key=lambda e: -abs(e.marginal_effect or 0.0)),
        also_appears_in=sorted(others, key=lambda e: e.slack),
        ruled_out=ruled_out,
        assumptions=compiled.record.assumptions,
        scenario=compiled.record.scenario_key,
    )
    explanation.narrative = _narrate(explanation, var.name)
    return explanation


def _parameter_evidence(spec_param, name: str) -> ParameterEvidence:
    if spec_param is None:
        return ParameterEvidence(parameter=name)
    sources = sorted(
        {v.origin.source for v in spec_param.values if v.origin is not None}
    )
    return ParameterEvidence(
        parameter=name, description=spec_param.description,
        unit=spec_param.unit, sources=sources,
    )


def _narrate(explanation: Explanation, variable_name: str) -> list[str]:
    """Plain-language summary, composed from the structured evidence above.

    Deliberately templated rather than generated: in an audited setting the
    sentence must be a faithful restatement of the numbers, and a restatement
    that can drift is worse than none.
    """
    lines: list[str] = []
    shown = explanation.label or explanation.decision
    lines.append(f"'{shown}' was set to {explanation.value:g}.")
    if explanation.meaning:
        lines.append(f"This means: {explanation.meaning.lower().rstrip('.')}.")
    if explanation.scenario:
        lines.append(f"The run used scenario '{explanation.scenario}', not the baseline data.")

    for name, amount in explanation.objective_contribution.items():
        lines.append(f"It contributes {amount:g} to the '{name}' objective.")

    if explanation.limited_by:
        lines.append(
            f"{len(explanation.limited_by)} constraint(s) involving it are binding — "
            "these are what stop the value moving further:"
        )
        for evidence in explanation.limited_by[:5]:
            named = evidence.label or evidence.key
            detail = f"  - {named}: {evidence.statement or evidence.name}"
            if evidence.marginal_effect:
                detail += (
                    f" — one more unit of headroom here would change "
                    f"'{evidence.marginal_objective}' by {evidence.marginal_effect:+g}"
                )
            lines.append(detail)
            if evidence.parameters:
                names = ", ".join(p.parameter for p in evidence.parameters)
                sources = sorted({s for p in evidence.parameters for s in p.sources})
                lines.append(
                    f"    driven by {names}"
                    + (f", sourced from {', '.join(sources)}" if sources else "")
                )
    else:
        lines.append(
            "No constraint involving it is binding; its value is driven by the "
            "objective and its own bounds."
        )

    if explanation.ruled_out:
        lines.append("Other options for it were removed by:")
        for evidence in explanation.ruled_out[:4]:
            lines.append(
                f"  - {evidence.statement or evidence.name} "
                f"({evidence.alternatives_removed} option(s) removed)"
            )
            attributed = sorted({
                source
                for parameter in evidence.parameters
                for source in parameter.sources
                if source != "user_input"
            })
            if attributed:
                lines.append(f"    including {', '.join(attributed)}")
    return lines
