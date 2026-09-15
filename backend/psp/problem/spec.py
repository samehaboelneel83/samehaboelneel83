"""The Problem Model — the business-facing layer.

This is what an analyst edits and what the organisation argues about. It is
deliberately *not* the IR: it carries the things a model needs to be defensible
rather than merely solvable — where each number came from, what each constraint
means in words, which assumptions it rests on, and which scenarios it should be
tested under.

The compiler turns a problem plus a scenario into an :class:`~psp.ir.IRModel`.
It never invents anything: every IR element traces back to an element here.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from psp.ir.expr import Binding, Expr, Pred, Rel
from psp.problem.hierarchy import Hierarchy
from psp.ir.model import Sense, SetKind, VarKind


class SourceRef(BaseModel):
    """Where a value came from. The first link in the provenance chain."""

    source: str
    fragment: str | None = None
    recorded_at: str | None = None
    confidence: float | None = None
    note: str | None = None


class ParameterValue(BaseModel):
    index: list[str] = Field(default_factory=list)
    value: float
    origin: SourceRef | None = None


class ProblemSet(BaseModel):
    """An index set, usually populated from the domain model."""

    name: str
    kind: SetKind = "label"
    elements: list[str] = Field(default_factory=list)
    entity_type: str | None = None
    from_entity_type: str | None = None
    """An entity type whose entities are this set, rather than a list written
    out. Filled in when the problem is bound, and what is stored is the result."""
    description: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    """How each element should read to a person, keyed by element.

    An integer set is the case that needs this: slot 23 has to be numeric for
    the compiler to do arithmetic on it, and has to read "Thu 13:45" for anyone
    looking at the answer. Elements missing from the map fall back to
    themselves, so a partial map is fine."""


class ProblemParameter(BaseModel):
    """A known quantity. Values carry their own provenance."""

    name: str
    index_sets: list[str] = Field(default_factory=list)
    values: list[ParameterValue] = Field(default_factory=list)
    default: float | None = None
    unit: str | None = None
    description: str | None = None
    derived_from: str | None = None
    """The hierarchy this table was computed from, when it was not written out.
    Kept so the language can write the tree back rather than its consequences."""
    from_attribute: str | None = None
    """An entity attribute these values are read from, rather than written out."""


class ProblemVariable(BaseModel):
    """A quantity the organisation gets to choose."""

    name: str
    index_sets: list[str] = Field(default_factory=list)
    kind: VarKind = "continuous"
    lb: float = 0.0
    ub: float | None = None
    description: str | None = None
    decision_meaning: str | None = None


class ProblemConstraint(BaseModel):
    """A restriction, stated both in words and in machine form.

    ``statement`` is not decoration: it is what an explanation quotes when the
    constraint turns out to be the binding one.
    """

    name: str
    statement: str
    forall: list[Binding] = Field(default_factory=list)
    where: Pred | None = None
    rel: Rel
    category: Literal[
        "physical", "policy", "regulatory", "operational", "modelling"
    ] = "operational"
    penalty: Expr | None = None
    """What one unit of violation costs. ``None`` means the constraint is hard
    and cannot be violated at any price, which is the default: a rule is only
    negotiable when someone says so and says what the price is.

    An expression rather than a number, so a price can be a parameter — and
    therefore something a scenario can move. "What if keeping requests mattered
    more?" is a question about a price, and it was unaskable while prices were
    literals."""
    when: Expr | None = None
    """A binary decision this rule waits on, or ``None`` for a rule that always
    applies."""
    when_is: float = 1.0
    rationale: str | None = None
    origin: SourceRef | None = None

    @property
    def soft(self) -> bool:
        return self.penalty is not None


class ProblemObjective(BaseModel):
    name: str
    statement: str
    sense: Sense
    expr: Expr
    weight: float = 1.0
    unit: str | None = None


class Assumption(BaseModel):
    """Something taken as true that the model cannot prove."""

    key: str
    statement: str
    rationale: str | None = None
    affects: list[str] = Field(default_factory=list)


class ScenarioOverride(BaseModel):
    parameter: str
    index: list[str] = Field(default_factory=list)
    value: float | None = None
    scale: float | None = None


class Scenario(BaseModel):
    """A named what-if. Scenarios never mutate the baseline problem."""

    key: str
    name: str
    description: str | None = None
    overrides: list[ScenarioOverride] = Field(default_factory=list)


class Uncertainty(BaseModel):
    """A declared range around a parameter, used by sampling and stress runs."""

    parameter: str
    index: list[str] = Field(default_factory=list)
    distribution: Literal["uniform", "triangular", "normal"] = "uniform"
    relative: bool = True
    """When true, ``low``/``high``/``mode`` are multipliers on the baseline value
    rather than absolute quantities, so one declaration survives a data refresh."""
    low: float | None = None
    high: float | None = None
    mode: float | None = None
    stddev: float | None = None


class StructureHint(BaseModel):
    """A claim about the problem's shape that a specialised solver can exploit.

    The compiler verifies the claim against the model before passing it on; an
    unverifiable hint is dropped with a warning rather than trusted.
    """

    kind: str
    arc_variable: str | None = None
    cost_parameter: str | None = None
    supply_parameter: str | None = None
    demand_parameter: str | None = None
    capacity_parameter: str | None = None
    tail_set: str | None = None
    head_set: str | None = None


class ProblemSpec(BaseModel):
    """A complete problem statement."""

    key: str
    name: str
    problem_type: str
    description: str | None = None
    sets: list[ProblemSet] = Field(default_factory=list)
    parameters: list[ProblemParameter] = Field(default_factory=list)
    variables: list[ProblemVariable] = Field(default_factory=list)
    constraints: list[ProblemConstraint] = Field(default_factory=list)
    objectives: list[ProblemObjective] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    hierarchies: list[Hierarchy] = Field(default_factory=list)
    scenarios: list[Scenario] = Field(default_factory=list)
    uncertainty: list[Uncertainty] = Field(default_factory=list)
    structure: StructureHint | None = None
    template_key: str | None = None
    template_inputs: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)

    def scenario(self, key: str) -> Scenario:
        for s in self.scenarios:
            if s.key == key:
                return s
        raise KeyError(f"unknown scenario '{key}'")

    def parameter(self, name: str) -> ProblemParameter:
        for p in self.parameters:
            if p.name == name:
                return p
        raise KeyError(f"unknown parameter '{name}'")
