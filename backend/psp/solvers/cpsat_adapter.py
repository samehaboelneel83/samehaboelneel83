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
from psp.solvers.base import (
    RATIONAL_SCALE_LIMIT, Capabilities, SolveOptions, SolverAdapter, SolveResult, SolveStatus,
)

MAX_SCALE = RATIONAL_SCALE_LIMIT


class CpSatAdapter(SolverAdapter):
    name = "cpsat"
    engine = "OR-Tools CP-SAT"

    def capabilities(self) -> Capabilities:
        return Capabilities(
            continuous=False, integer=True, binary=True, duals=False,
            quadratic_objective=True, requires_bounded_integers=True,
            requires_rational_data=True,
        )

    def solve(self, model: FlatModel, options: SolveOptions) -> SolveResult:
        from ortools.sat.python import cp_model

        cp = cp_model.CpModel()
        variables = {}
        bounds: dict[str, tuple[int, int]] = {}
        for v in model.variables:
            lb, ub = int(math.ceil(v.lb)), int(math.floor(v.ub))
            variables[v.key] = cp.NewIntVar(lb, ub, v.key)
            bounds[v.key] = (lb, ub)

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

        # A degree-two objective is expressed the way CP-SAT expresses one: an
        # auxiliary variable per product, constrained to equal it. That is why
        # convexity is not a question here as it is for HiGHS — nothing is being
        # minimised by convex optimisation, it is search over integers.
        products = []
        for term in model.objective.quadratic:
            lo, hi = _product_bounds(bounds[term.i], bounds[term.j])
            product = cp.NewIntVar(lo, hi, f"({term.i})*({term.j})")
            cp.AddMultiplicationEquality(product, [variables[term.i], variables[term.j]])
            products.append((term.coef, product))

        obj_scale = _scale_for(
            list(model.objective.terms.values()) + [c for c, _ in products] or [0.0]
        )
        if model.objective.terms or products:
            cp.Minimize(
                sum(_exact(coef, obj_scale) * variables[key]
                    for key, coef in model.objective.terms.items())
                + sum(_exact(coef, obj_scale) * product for coef, product in products)
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
        if model.objective.terms or model.objective.quadratic:
            result.objective_value = solver.ObjectiveValue() / obj_scale + model.objective.constant
            result.best_bound = solver.BestObjectiveBound() / obj_scale + model.objective.constant
        else:
            result.objective_value = model.objective.constant
        return result


def _product_bounds(a: tuple[int, int], b: tuple[int, int]) -> tuple[int, int]:
    """Range of ``a * b``, taken from the corners — a product of two ranges is
    not monotone once either can go negative, so all four have to be looked at.

    The bounds come from the flat model rather than from the CP-SAT variable:
    the solver's own proto is not a reliable place to read a domain back from.
    """
    corners = [x * y for x in a for y in b]
    return min(corners), max(corners)


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
