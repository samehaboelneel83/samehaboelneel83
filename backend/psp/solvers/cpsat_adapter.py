"""OR-Tools CP-SAT adapter — the primary engine for discrete models.

CP-SAT is integral-only and works in exact integer arithmetic, so the adapter
has one real job: find a single scale factor that turns every rational
coefficient in the model into an integer without distorting the problem. If no
such factor exists within tolerance the adapter refuses rather than rounding
silently — a wrong answer is worse than no answer in an audited system.
"""

from __future__ import annotations

import math
import time
from fractions import Fraction

from psp.compiler.flat import FlatModel
from psp.solvers.base import Capabilities, SolveOptions, SolveResult, SolveStatus, SolverAdapter

MAX_SCALE = 10**6


class CpSatAdapter(SolverAdapter):
    name = "cpsat"
    engine = "OR-Tools CP-SAT"

    def capabilities(self) -> Capabilities:
        return Capabilities(
            continuous=False, integer=True, binary=True, duals=False,
            requires_bounded_integers=True,
        )

    def solve(self, model: FlatModel, options: SolveOptions) -> SolveResult:
        from ortools.sat.python import cp_model

        cp = cp_model.CpModel()
        variables = {}
        for v in model.variables:
            lb, ub = int(math.ceil(v.lb)), int(math.floor(v.ub))
            variables[v.key] = cp.NewIntVar(lb, ub, v.key)

        scales: dict[str, int] = {}
        for c in model.constraints:
            scale = _scale_for([*c.terms.values(), c.rhs])
            scales[c.key] = scale
            expr = sum(
                _exact(coef, scale) * variables[key] for key, coef in c.terms.items()
            )
            rhs = _exact(c.rhs, scale)
            if c.op == "le":
                cp.Add(expr <= rhs)
            elif c.op == "ge":
                cp.Add(expr >= rhs)
            else:
                cp.Add(expr == rhs)

        obj_scale = _scale_for(list(model.objective.terms.values()) or [0.0])
        if model.objective.terms:
            cp.Minimize(
                sum(_exact(coef, obj_scale) * variables[key]
                    for key, coef in model.objective.terms.items())
            )

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = float(options.time_limit_seconds)
        solver.parameters.num_workers = max(1, int(options.threads))
        solver.parameters.random_seed = int(options.seed)
        solver.parameters.log_search_progress = bool(options.log)
        if options.relative_gap is not None:
            solver.parameters.relative_gap_limit = float(options.relative_gap)

        started = time.perf_counter()
        status = solver.Solve(cp)
        elapsed = time.perf_counter() - started

        mapped = {
            cp_model.OPTIMAL: SolveStatus.OPTIMAL,
            cp_model.FEASIBLE: SolveStatus.FEASIBLE,
            cp_model.INFEASIBLE: SolveStatus.INFEASIBLE,
            cp_model.MODEL_INVALID: SolveStatus.ERROR,
            cp_model.UNKNOWN: SolveStatus.TIMEOUT,
        }.get(status, SolveStatus.ERROR)

        result = SolveResult(
            status=mapped,
            solver=self.name,
            wall_time_seconds=elapsed,
            message=solver.StatusName(status),
            diagnostics={
                "engine": self.engine,
                "objective_scale": obj_scale,
                "max_constraint_scale": max(scales.values(), default=1),
                "branches": solver.NumBranches(),
                "conflicts": solver.NumConflicts(),
                **model.stats(),
            },
        )
        if not mapped.has_solution:
            return result
        result.values = {key: float(solver.Value(var)) for key, var in variables.items()}
        if model.objective.terms:
            result.objective_value = solver.ObjectiveValue() / obj_scale + model.objective.constant
            result.best_bound = solver.BestObjectiveBound() / obj_scale + model.objective.constant
        else:
            result.objective_value = model.objective.constant
        return result


def _scale_for(numbers: list[float]) -> int:
    """Smallest common multiplier making every number integral."""
    denominator = 1
    for x in numbers:
        frac = Fraction(x).limit_denominator(MAX_SCALE)
        if abs(float(frac) - x) > 1e-9 * max(1.0, abs(x)):
            raise ValueError(
                f"coefficient {x!r} cannot be represented exactly as a rational with "
                f"denominator <= {MAX_SCALE}; CP-SAT requires integral data"
            )
        denominator = denominator * frac.denominator // math.gcd(denominator, frac.denominator)
        if denominator > MAX_SCALE:
            raise ValueError(
                "model coefficients need a scale factor above "
                f"{MAX_SCALE}; use the 'highs' solver for this model"
            )
    return denominator


def _exact(x: float, scale: int) -> int:
    scaled = x * scale
    nearest = round(scaled)
    if abs(scaled - nearest) > 1e-6:
        raise ValueError(f"coefficient {x!r} is not integral at scale {scale}")
    return int(nearest)
