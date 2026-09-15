"""The Model IR: the solver-independent normal form of a problem.

Everything upstream (domain model, problem model, templates, DSL) targets this
structure, and everything downstream (OR-Tools, HiGHS, NetworkX) consumes it.
It is deliberately *indexed* rather than flat — a flat matrix loses the
structure that explanations and structure-recognising solvers need.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from psp.ir.expr import Binding, Expr, Pred, Rel

SetKind = Literal["label", "int"]
VarKind = Literal["binary", "integer", "continuous"]
Sense = Literal["minimize", "maximize"]


class IRSet(BaseModel):
    """An index set. Integer sets allow arithmetic on their indices."""

    name: str
    kind: SetKind = "label"
    elements: list[str] = Field(default_factory=list)
    description: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    """Human-readable form of each element. Carried on the IR rather than
    looked up from the problem at render time, so a model stored today can
    still be explained readably in a year."""

    @field_validator("elements")
    @classmethod
    def _unique(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("set elements must be unique")
        return v

    @classmethod
    def integers(cls, name: str, lo: int, hi: int, **kw) -> IRSet:
        """Inclusive integer range ``[lo, hi]``."""
        return cls(name=name, kind="int", elements=[str(i) for i in range(lo, hi + 1)], **kw)


class IRParam(BaseModel):
    """A named, indexed table of constants.

    ``values`` is keyed by the index tuple joined with ``|`` so the whole model
    stays JSON-serialisable; use :meth:`key` rather than building keys by hand.
    """

    name: str
    index_sets: list[str] = Field(default_factory=list)
    values: dict[str, float] = Field(default_factory=dict)
    default: float | None = None
    unit: str | None = None
    description: str | None = None

    @staticmethod
    def key(index: tuple[str, ...] | list[str]) -> str:
        return "|".join(index)

    def get(self, index: tuple[str, ...]) -> float:
        v = self.values.get(self.key(index))
        if v is None:
            if self.default is None:
                where = f"[{', '.join(index)}]" if index else ""
                raise KeyError(f"parameter '{self.name}{where}' has no value and no default")
            return self.default
        return v


class IRVar(BaseModel):
    """A decision-variable family, indexed by ``index_sets``."""

    name: str
    index_sets: list[str] = Field(default_factory=list)
    kind: VarKind = "continuous"
    lb: float = 0.0
    ub: float | None = None
    description: str | None = None

    def bounds(self) -> tuple[float, float | None]:
        if self.kind == "binary":
            return 0.0, 1.0
        return self.lb, self.ub


class IRConstraint(BaseModel):
    """A constraint family: ``rel`` instantiated once per ``forall`` tuple."""

    name: str
    forall: list[Binding] = Field(default_factory=list)
    where: Pred | None = None
    rel: Rel
    statement: str | None = None
    origin: str | None = None
    penalty: float | None = None
    """Cost of one unit of violation, or ``None`` for a rule that holds
    absolutely. A penalty makes the family soft: the compiler gives each of its
    rows room to be broken and charges the objective for using it."""

    @property
    def soft(self) -> bool:
        return self.penalty is not None


class IRObjective(BaseModel):
    """A weighted objective. Several are combined into one weighted sum."""

    name: str
    sense: Sense
    expr: Expr
    weight: float = 1.0
    unit: str | None = None


class IRModel(BaseModel):
    """A complete, self-contained, solver-independent model."""

    name: str
    sets: list[IRSet] = Field(default_factory=list)
    parameters: list[IRParam] = Field(default_factory=list)
    variables: list[IRVar] = Field(default_factory=list)
    constraints: list[IRConstraint] = Field(default_factory=list)
    objectives: list[IRObjective] = Field(default_factory=list)
    structure: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)

    def set(self, name: str) -> IRSet:
        for s in self.sets:
            if s.name == name:
                return s
        raise KeyError(f"unknown set '{name}'")

    def param(self, name: str) -> IRParam:
        for p in self.parameters:
            if p.name == name:
                return p
        raise KeyError(f"unknown parameter '{name}'")

    def var(self, name: str) -> IRVar:
        for v in self.variables:
            if v.name == name:
                return v
        raise KeyError(f"unknown variable '{name}'")

    def fingerprint(self) -> str:
        """Content hash — two models with the same fingerprint compile identically."""
        import hashlib

        payload = self.model_dump_json(exclude={"metadata"}).encode()
        return hashlib.sha256(payload).hexdigest()
