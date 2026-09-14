"""HiGHS adapter — the default LP/MIP engine.

HiGHS is the workhorse: it accepts every model the compiler can produce, and
for pure LPs it returns dual values, which the explanation layer turns into
shadow prices ("relaxing this constraint by one unit is worth X").
"""

from __future__ import annotations

import time

from psp.compiler.flat import FlatModel
from psp.solvers.base import Capabilities, SolveOptions, SolverAdapter, SolveResult, SolveStatus

_STATUS = {
    "Optimal": SolveStatus.OPTIMAL,
    "Infeasible": SolveStatus.INFEASIBLE,
    "Primal infeasible": SolveStatus.INFEASIBLE,
    "Unbounded": SolveStatus.UNBOUNDED,
    "Primal unbounded": SolveStatus.UNBOUNDED,
    "Time limit reached": SolveStatus.TIMEOUT,
    "Iteration limit reached": SolveStatus.TIMEOUT,
    "Solution limit reached": SolveStatus.FEASIBLE,
}


class HighsAdapter(SolverAdapter):
    name = "highs"
    engine = "HiGHS"

    def capabilities(self) -> Capabilities:
        return Capabilities(continuous=True, integer=True, binary=True, duals=True)

    def solve(self, model: FlatModel, options: SolveOptions) -> SolveResult:
        import highspy
        import numpy as np

        inf = highspy.kHighsInf
        h = highspy.Highs()
        h.setOptionValue("output_flag", bool(options.log))
        h.setOptionValue("time_limit", float(options.time_limit_seconds))
        h.setOptionValue("threads", int(options.threads))
        h.setOptionValue("random_seed", int(options.seed))
        if options.relative_gap is not None:
            h.setOptionValue("mip_rel_gap", float(options.relative_gap))

        col_of = {v.key: i for i, v in enumerate(model.variables)}
        n = len(model.variables)
        cost = np.zeros(n)
        for key, coef in model.objective.terms.items():
            cost[col_of[key]] = coef
        lower = np.array([v.lb for v in model.variables], dtype=float)
        upper = np.array([inf if v.ub is None else v.ub for v in model.variables], dtype=float)

        empty_i = np.array([], dtype=np.int32)
        h.addCols(n, cost, lower, upper, 0, empty_i, empty_i, np.array([], dtype=float))

        integral = [i for i, v in enumerate(model.variables) if v.kind in ("binary", "integer")]
        if integral:
            h.changeColsIntegrality(
                len(integral),
                np.array(integral, dtype=np.int32),
                np.array([highspy.HighsVarType.kInteger] * len(integral)),
            )

        # Rows are added in one pass in CSR form; ordering matches model.constraints
        # so a returned dual can be attributed back to its IR constraint instance.
        starts, indices, values, lo, hi = [], [], [], [], []
        for c in model.constraints:
            starts.append(len(indices))
            for key, coef in c.terms.items():
                indices.append(col_of[key])
                values.append(coef)
            if c.op == "le":
                lo.append(-inf)
                hi.append(c.rhs)
            elif c.op == "ge":
                lo.append(c.rhs)
                hi.append(inf)
            else:
                lo.append(c.rhs)
                hi.append(c.rhs)
        if model.constraints:
            h.addRows(
                len(model.constraints),
                np.array(lo, dtype=float),
                np.array(hi, dtype=float),
                len(indices),
                np.array(starts, dtype=np.int32),
                np.array(indices, dtype=np.int32),
                np.array(values, dtype=float),
            )

        h.changeObjectiveSense(highspy.ObjSense.kMinimize)
        if model.objective.constant:
            h.changeObjectiveOffset(float(model.objective.constant))

        started = time.perf_counter()
        h.run()
        elapsed = time.perf_counter() - started

        status_text = h.modelStatusToString(h.getModelStatus())
        status = _STATUS.get(status_text, SolveStatus.ERROR)
        info = h.getInfo()

        result = SolveResult(
            status=status,
            solver=self.name,
            wall_time_seconds=elapsed,
            message=status_text,
            diagnostics={"engine": self.engine, "model_status": status_text, **model.stats()},
        )
        if not status.has_solution:
            return result

        solution = h.getSolution()
        result.objective_value = float(info.objective_function_value)
        result.values = {
            v.key: float(solution.col_value[i]) for i, v in enumerate(model.variables)
        }
        if integral:
            result.best_bound = float(info.mip_dual_bound)
            result.gap = float(info.mip_gap)
        elif model.constraints and solution.row_dual is not None:
            # Shadow prices are only meaningful for the continuous relaxation.
            result.duals = {
                c.key: float(solution.row_dual[i]) for i, c in enumerate(model.constraints)
            }
        return result
