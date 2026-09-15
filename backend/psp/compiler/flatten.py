"""IR -> FlatModel.

Deterministic: the same IR always produces the same flat model, with the same
row and column ordering. That is what makes runs reproducible and diffable, and
what lets a solution be replayed against the exact model that produced it.
"""

from __future__ import annotations

from psp.compiler.errors import CompileError, DomainError
from psp.compiler.evaluator import Affine, Evaluator, Quadratic
from psp.compiler.flat import FlatConstraint, FlatModel, FlatObjective, FlatVar, QuadTerm
from psp.ir.model import IRModel, IRVar


def flatten(model: IRModel) -> FlatModel:
    """Expand every constraint family and objective into a flat linear system."""
    columns: dict[str, FlatVar] = {}

    def materialise(decl: IRVar, index: list[str], key: str) -> None:
        if key in columns:
            return
        lb, ub = decl.bounds()
        columns[key] = FlatVar(
            key=key, name=decl.name, index=list(index), kind=decl.kind, lb=lb, ub=ub
        )

    ev = Evaluator(model, on_var=materialise)
    rows: list[FlatConstraint] = []

    for c in model.constraints:
        seen: set[str] = set()
        for env in ev.iterate(c.forall, {}, c.where):
            index = [env[b.index][0] for b in c.forall]
            key = f"{c.name}[{','.join(index)}]" if index else c.name
            if key in seen:
                raise CompileError(f"duplicate constraint instance '{key}'", where=c.name)
            seen.add(key)
            try:
                lhs = ev.affine(c.rel.lhs, env)
                rhs = ev.affine(c.rel.rhs, env)
            except CompileError as exc:
                raise exc.at(f"constraint '{key}'") from exc
            body = _num(lhs, c.name) - _num(rhs, c.name)
            if not body.terms:
                # A constraint with no variables is a data assertion: either it
                # holds (drop it) or the instance is infeasible before solving.
                if not _holds(c.rel.op, body.constant):
                    raise CompileError(
                        f"constraint '{key}' contains no decision variables and is "
                        f"violated by the data ({body.constant:g} {c.rel.op} 0 is false)",
                        where=c.name,
                    )
                continue
            rows.append(
                FlatConstraint(
                    key=key,
                    name=c.name,
                    terms=dict(body.terms),
                    op=c.rel.op,
                    rhs=-body.constant,
                    index=index,
                    statement=c.statement,
                )
            )

    objective, components = _flatten_objectives(model, ev)

    # Guarantee every declared variable that the objective or a bound needs is a
    # column, then order columns deterministically.
    ordered = sorted(columns.values(), key=lambda v: (v.name, v.index))
    _check_bounds(ordered)

    return FlatModel(
        name=model.name,
        variables=ordered,
        constraints=rows,
        objective=objective,
        structure=dict(model.structure),
        metadata={
            "ir_fingerprint": model.fingerprint(),
            "objective_components": components,
            **model.metadata,
        },
    )


def _flatten_objectives(model: IRModel, ev: Evaluator) -> tuple[FlatObjective, list[dict]]:
    """Combine weighted objectives into a single minimisation.

    Objectives are folded in the quadratic algebra. Most stay linear and the
    quadratic map comes back empty, which costs one dictionary per objective —
    not per row, which is where the compiler's time actually goes.
    """
    total = Quadratic()
    components: list[dict] = []
    for o in model.objectives:
        try:
            quad = _num(ev.affine(o.expr, {}, max_degree=2), o.name)
        except CompileError as exc:
            raise exc.at(f"objective '{o.name}'") from exc
        # Normalise to minimisation so adapters never need a sense switch.
        sign = 1.0 if o.sense == "minimize" else -1.0
        total = total + quad.scale(sign * o.weight)
        components.append(
            {
                "name": o.name,
                "sense": o.sense,
                "weight": o.weight,
                "unit": o.unit,
                "terms": dict(quad.terms),
                "quadratic": [[i, j, c] for (i, j), c in sorted(quad.quad.items())],
                "constant": quad.constant,
            }
        )
    if not components:
        # A pure satisfaction problem: any feasible point will do.
        components.append(
            {"name": "feasibility", "sense": "minimize", "weight": 0.0,
             "terms": {}, "quadratic": [], "constant": 0.0}
        )
    quadratic = [
        QuadTerm(i=i, j=j, coef=c) for (i, j), c in sorted(total.quad.items())
    ]
    objective = FlatObjective(
        sense="minimize", terms=dict(total.terms), quadratic=quadratic,
        constant=total.constant, components=components,
        convex=_is_convex(quadratic) if quadratic else None,
    )
    return objective, components


# Above this many distinct variables the exact test is not worth its cubic
# cost; the answer becomes "not established" and the engine that cares is told
# so rather than being handed a claim the compiler did not actually check.
CONVEXITY_LIMIT = 1000


def _is_convex(quadratic: list[QuadTerm]) -> bool | None:
    """Is the composite objective convex, as the minimisation it has become?

    HiGHS refuses a non-convex quadratic with a bare error and no diagnosis, so
    the question is answered here, where the objective still has a name.
    """
    keys = sorted({k for term in quadratic for k in (term.i, term.j)})
    if len(keys) > CONVEXITY_LIMIT:
        return None
    try:
        import numpy as np
    except ImportError:  # pragma: no cover - numpy ships with the solvers
        return None

    at = {k: n for n, k in enumerate(keys)}
    hessian = np.zeros((len(keys), len(keys)))
    for term in quadratic:
        a, b = at[term.i], at[term.j]
        if a == b:
            # d2/dx2 of c*x*x is 2c; the off-diagonals are c in both places.
            hessian[a][a] += 2.0 * term.coef
        else:
            hessian[a][b] += term.coef
            hessian[b][a] += term.coef
    smallest = float(np.linalg.eigvalsh(hessian)[0])
    # A tolerance proportional to the matrix, so a objective that is convex but
    # only just is not failed by rounding in the eigenvalue solver.
    scale = max(1.0, float(np.abs(hessian).max()))
    return smallest >= -1e-8 * scale


def _check_bounds(columns: list[FlatVar]) -> None:
    for v in columns:
        if v.ub is not None and v.ub < v.lb:
            raise DomainError(f"variable '{v.key}' has ub {v.ub} below lb {v.lb}")


def _holds(op: str, lhs_minus_rhs: float, eps: float = 1e-9) -> bool:
    return {
        "le": lhs_minus_rhs <= eps,
        "ge": lhs_minus_rhs >= -eps,
        "eq": abs(lhs_minus_rhs) <= eps,
    }[op]


def _num(v, where: str) -> Affine | Quadratic:
    if isinstance(v, str):
        raise DomainError(f"label {v!r} used where a number is required", where=where)
    return v
