"""The solver boundary.

Adapters translate a :class:`~psp.compiler.flat.FlatModel` into a concrete
engine and translate the answer back. They know nothing about problems,
domains, templates or the database — swapping an engine is a change confined to
this package.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from psp.compiler.flat import FlatModel


class SolveStatus(StrEnum):
    OPTIMAL = "optimal"
    FEASIBLE = "feasible"
    INFEASIBLE = "infeasible"
    UNBOUNDED = "unbounded"
    TIMEOUT = "timeout"
    UNSUPPORTED = "unsupported"
    ERROR = "error"

    @property
    def has_solution(self) -> bool:
        return self in (SolveStatus.OPTIMAL, SolveStatus.FEASIBLE, SolveStatus.TIMEOUT)


class Capabilities(BaseModel):
    """What an engine can accept. Checked before a run, not discovered during one."""

    continuous: bool = True
    integer: bool = False
    binary: bool = False
    duals: bool = False
    quadratic_objective: bool = False
    requires_convex_quadratic: bool = False
    """Set where the engine minimises a quadratic by convex optimisation, so a
    non-convex objective is not merely slow but meaningless to it."""
    requires_structure: str | None = None
    requires_bounded_integers: bool = False
    requires_rational_data: bool = False
    """Set where the engine works in exact integer arithmetic and so needs every
    coefficient to scale to a whole number within :data:`RATIONAL_SCALE_LIMIT`."""


#: Workers a solve gets unless it asks for otherwise. Fixed, not taken from the
#: machine: CP-SAT's search depends on how many workers it has, so a default
#: that read the core count would make the plan depend on which computer ran it.
#: Four is enough to change the answer from "feasible" to "proved optimal" on
#: the models here and small enough to leave a server room to serve.
DEFAULT_THREADS = 4

#: Largest common multiplier an exact-arithmetic engine may need to turn every
#: coefficient in a model into a whole number.
RATIONAL_SCALE_LIMIT = 10**6


class SolveOptions(BaseModel):
    time_limit_seconds: float = 30.0
    relative_gap: float | None = None
    threads: int = DEFAULT_THREADS
    seed: int = 0
    log: bool = False
    extra: dict = Field(default_factory=dict)


class SolveResult(BaseModel):
    status: SolveStatus
    solver: str
    objective_value: float | None = None
    best_bound: float | None = None
    gap: float | None = None
    values: dict[str, float] = Field(default_factory=dict)
    duals: dict[str, float] = Field(default_factory=dict)
    wall_time_seconds: float = 0.0
    message: str | None = None
    log: list[str] = Field(default_factory=list)
    diagnostics: dict = Field(default_factory=dict)


def _unrepresentable(model: FlatModel) -> float | None:
    """The first coefficient an exact-arithmetic engine could not take.

    Checked here rather than found inside a solve, because an engine that
    cannot represent a model has not accepted it — and until this was a
    capability, the registry would choose CP-SAT, fail mid-run, and return an
    error while a capable engine sat unused. Distinct values only: models carry
    tens of thousands of coefficients and a handful of different ones.
    """
    from fractions import Fraction

    values = set()
    for row in model.constraints:
        values.update(row.terms.values())
        values.add(row.rhs)
    values.update(model.objective.terms.values())
    values.update(term.coef for term in model.objective.quadratic)
    for value in values:
        fraction = Fraction(value).limit_denominator(RATIONAL_SCALE_LIMIT)
        if abs(float(fraction) - value) > 1e-9 * max(1.0, abs(value)):
            return value
        if fraction.denominator > RATIONAL_SCALE_LIMIT:
            return value
    return None


class SolverAdapter:
    """Base class for engine adapters."""

    name: str = "abstract"
    engine: str = "abstract"

    def capabilities(self) -> Capabilities:  # pragma: no cover - interface
        raise NotImplementedError

    def accepts(self, model: FlatModel) -> tuple[bool, str | None]:
        """Return (accepted, reason-if-not). Pure, cheap, no engine import."""
        caps = self.capabilities()
        kinds = {v.kind for v in model.variables}
        if model.objective.is_quadratic:
            if not caps.quadratic_objective:
                return False, f"{self.name} takes a linear objective only"
            if caps.requires_convex_quadratic:
                if kinds & {"integer", "binary"}:
                    return False, (
                        f"{self.name} has no mixed-integer quadratic mode; a "
                        "quadratic objective needs either a fully continuous "
                        "model here or a fully discrete one for cpsat"
                    )
                if model.objective.convex is False:
                    return False, (
                        f"{self.name} minimises a quadratic objective only where it "
                        "is convex, and this one is not"
                    )
        if "integer" in kinds and not caps.integer:
            return False, f"{self.name} does not support integer variables"
        if "binary" in kinds and not caps.binary:
            return False, f"{self.name} does not support binary variables"
        if "continuous" in kinds and not caps.continuous:
            return False, f"{self.name} requires a fully discrete model"
        if caps.requires_structure:
            found = model.structure.get("kind")
            if found != caps.requires_structure:
                return False, (
                    f"{self.name} requires structure '{caps.requires_structure}', "
                    f"model declares '{found or 'none'}'"
                )
        if caps.requires_bounded_integers:
            for v in model.variables:
                if v.kind in ("integer", "binary") and v.ub is None:
                    return False, f"{self.name} requires a finite upper bound on '{v.key}'"
        if caps.requires_rational_data:
            awkward = _unrepresentable(model)
            if awkward is not None:
                return False, (
                    f"{self.name} works in exact integer arithmetic, and the "
                    f"coefficient {awkward!r} does not scale to a whole number "
                    f"within {RATIONAL_SCALE_LIMIT}"
                )
        return True, None

    def solve(self, model: FlatModel, options: SolveOptions) -> SolveResult:  # pragma: no cover
        raise NotImplementedError
