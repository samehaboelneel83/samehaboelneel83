"""The flat model: what a solver adapter actually receives.

The flat model is a plain linear system — variables with bounds, constraints
as sparse rows, one objective. Every row keeps a ``source`` back-pointer to the
IR constraint and the index tuple it came from, which is what makes an answer
to "why is this decision what it is?" possible without an LLM.

The one departure from linearity is the objective, which may carry degree-two
terms. Rows never do: a quadratic constraint is a different class of problem,
and refusing it keeps every row a sparse line a dual can be attached to.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from psp.ir.model import Sense, VarKind

FlatRelOp = Literal["le", "ge", "eq"]


class FlatVar(BaseModel):
    key: str
    name: str
    index: list[str] = Field(default_factory=list)
    kind: VarKind
    lb: float
    ub: float | None = None
    role: Literal["decision", "violation"] = "decision"
    """``violation`` marks a column the compiler added to let a soft constraint
    be broken. It is a real column to the solver and not a decision to anyone
    else, so it is reported as a violation rather than as something chosen."""


class FlatConstraint(BaseModel):
    key: str
    name: str
    terms: dict[str, float]
    op: FlatRelOp
    rhs: float
    index: list[str] = Field(default_factory=list)
    statement: str | None = None
    penalty: float | None = None
    violation_keys: list[str] = Field(default_factory=list)
    """Columns in ``terms`` that measure how far this row is broken. They are
    named so that reporting can subtract them back out: the activity worth
    showing is the one the model stated, not the one the slack made feasible."""


class QuadTerm(BaseModel):
    """``coef * i * j``. ``i == j`` is a square; ``i < j`` a cross term, held
    once rather than twice so the coefficient means the same thing either way."""

    i: str
    j: str
    coef: float


class FlatObjective(BaseModel):
    sense: Sense = "minimize"
    terms: dict[str, float] = Field(default_factory=dict)
    quadratic: list[QuadTerm] = Field(default_factory=list)
    constant: float = 0.0
    components: list[dict] = Field(default_factory=list)
    convex: bool | None = None
    """Whether the composite objective is convex as a minimisation. ``None``
    when there is nothing quadratic to ask the question about. Decided at
    compile time because an engine that cannot take a non-convex objective
    should say so before it runs, not fail obscurely inside one."""

    @property
    def is_quadratic(self) -> bool:
        return bool(self.quadratic)


class FlatModel(BaseModel):
    """A solver-ready linear (or mixed-integer) program."""

    name: str
    variables: list[FlatVar] = Field(default_factory=list)
    constraints: list[FlatConstraint] = Field(default_factory=list)
    objective: FlatObjective = Field(default_factory=FlatObjective)
    structure: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)

    @property
    def var_index(self) -> dict[str, FlatVar]:
        return {v.key: v for v in self.variables}

    def signature(self) -> str:
        """Hash of the system itself: columns, rows, objective, and nothing else.

        Distinct from the IR fingerprint, which hashes the problem model as it
        happens to be spelled — its prose, the order values were written in, and
        every field the IR has today. That makes the fingerprint right for "is
        this the same stored model" and wrong for "does this still solve the
        same problem", because adding a field to the IR moves every fingerprint
        in existence while changing nothing anyone solves.

        This is the second question, so it is built from a projection that only
        the system can move.
        """
        import hashlib
        import json

        payload = {
            "columns": sorted(
                (v.key, v.kind, v.lb, v.ub, v.role) for v in self.variables
            ),
            "rows": sorted(
                (c.key, c.op, c.rhs, sorted(c.terms.items()), c.penalty)
                for c in self.constraints
            ),
            "objective": {
                "sense": self.objective.sense,
                "terms": sorted(self.objective.terms.items()),
                "quadratic": sorted(
                    (q.i, q.j, q.coef) for q in self.objective.quadratic
                ),
                "constant": self.objective.constant,
            },
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()

    def stats(self) -> dict:
        kinds: dict[str, int] = {}
        for v in self.variables:
            kinds[v.kind] = kinds.get(v.kind, 0) + 1
        stats = {
            "variables": len(self.variables),
            "constraints": len(self.constraints),
            "nonzeros": sum(len(c.terms) for c in self.constraints),
            "variable_kinds": kinds,
            "is_integer": any(v.kind in ("binary", "integer") for v in self.variables),
            "is_quadratic": self.objective.is_quadratic,
        }
        if self.objective.is_quadratic:
            stats["quadratic_terms"] = len(self.objective.quadratic)
            stats["objective_convex"] = self.objective.convex
        return stats
