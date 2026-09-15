"""IR -> FlatModel.

Deterministic: the same IR always produces the same flat model, with the same
row and column ordering. That is what makes runs reproducible and diffable, and
what lets a solution be replayed against the exact model that produced it.
"""

from __future__ import annotations

import math

from psp.compiler.errors import CompileError, DomainError
from psp.compiler.evaluator import Affine, Evaluator, Quadratic
from psp.compiler.flat import FlatConstraint, FlatModel, FlatObjective, FlatVar, QuadTerm
from psp.ir.model import IRConstraint, IRModel, IRVar


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
    # Slack column key -> what one unit of it costs. Collected while the rows
    # are expanded and charged to the objective once they all exist.
    penalties: dict[str, float] = {}

    def price_of(c: IRConstraint, env: dict, key: str) -> float:
        """What one unit of violation costs this row.

        Evaluated per row rather than read off the family, so a price can be a
        parameter — which is what lets a scenario ask "what if keeping this
        mattered more?" without editing the model.
        """
        priced = ev.affine(c.penalty, env, allow_vars=False)
        if isinstance(priced, str) or not priced.is_constant:
            raise CompileError(
                f"the penalty on '{c.name}' is not a fixed number for this rule",
                where=key,
            )
        if priced.constant <= 0:
            raise CompileError(
                f"a penalty of {priced.constant:g} makes '{c.name}' free to break",
                where=key,
            )
        return priced.constant

    def give_room(
        c: IRConstraint, key: str, body, price: float, rhs: float
    ) -> tuple[dict[str, float], list[str]]:
        """Add the columns that let one row of a soft family be broken.

        A ``<=`` row can only be broken upwards and a ``>=`` row only
        downwards, so each needs one column. An equality can miss in either
        direction and needs two, which is also why a violation is reported as a
        distance rather than a signed residual.
        """
        kind = _violation_kind(c, body, columns)
        reach = _row_reach(body, columns)
        added: list[str] = []
        terms: dict[str, float] = {}
        for role, coefficient in _violation_columns(c.rel.op):
            slack = f"~{role}[{key}]"
            columns[slack] = FlatVar(
                key=slack, name=f"~{role}", index=[c.name, *_index_of(key, c.name)],
                # Against the row's actual right-hand side, which a condition
                # will have moved. Reading it off the body instead leaves the
                # bound valid but slack, and the bound exists to be tight.
                kind=kind, lb=0.0, ub=_violation_bound(role, reach, rhs),
                role="violation",
            )
            terms[slack] = coefficient
            penalties[slack] = price
            added.append(slack)
        return terms, added

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
            op, rhs_value = c.rel.op, -body.constant
            if c.when is not None:
                body, op, rhs_value = _apply_condition(c, body, env, ev, columns, key)

            terms = dict(body.terms)
            violation_keys: list[str] = []
            price: float | None = None
            if c.soft:
                price = price_of(c, env, key)
                room, violation_keys = give_room(c, key, body, price, rhs_value)
                terms.update(room)
            rows.append(
                FlatConstraint(
                    key=key,
                    name=c.name,
                    terms=terms,
                    op=op,
                    rhs=rhs_value,
                    index=index,
                    statement=c.statement,
                    penalty=price,
                    violation_keys=violation_keys,
                )
            )

    objective, components = _flatten_objectives(model, ev, penalties)

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


def _violation_columns(op: str) -> list[tuple[str, float]]:
    """Which slack columns a row of this shape needs, and with what sign.

    ``lhs - shortfall <= rhs`` lets the left side run over; ``lhs + shortfall
    >= rhs`` lets it fall under; an equality gets one of each. In every case
    the column itself is non-negative, so its value *is* the violation.
    """
    if op == "le":
        return [("over", -1.0)]
    if op == "ge":
        return [("under", 1.0)]
    return [("under", 1.0), ("over", -1.0)]


def _violation_kind(c: IRConstraint, body, columns: dict[str, FlatVar]) -> str:
    """Integer where the row can only be missed by whole numbers.

    This is not a refinement. A continuous slack added to an otherwise discrete
    model makes it mixed, and a mixed model is one CP-SAT will refuse — so a
    soft constraint would quietly cost a discrete problem its engine.
    """
    integral = all(
        columns[key].kind in ("binary", "integer") and float(coef).is_integer()
        for key, coef in body.terms.items()
        if key in columns
    )
    return "integer" if integral and float(body.constant).is_integer() else "continuous"


def _apply_condition(c, body, env, ev, columns: dict[str, FlatVar], key: str):
    """Rewrite one row of a conditional family so it applies only when it should.

    ``if y then body <= 0`` becomes ``body + M*y <= M``: at ``y = 1`` it is the
    rule, and at ``y = 0`` it says ``body <= M``, which is no restriction so
    long as M is at least as large as ``body`` can ever be.

    That "so long as" is the whole difficulty with a big-M, and the reason this
    platform refuses to invent one. Here it is not invented: M is the row\'s own
    reach, read off the bounds of the columns the row is built from. Where those
    bounds do not prove a value, the compiler says so and stops rather than
    choosing a number that would quietly make the rule toothless — or, if it
    chose too small a one, quietly change what the model means.
    """
    condition = ev.affine(c.when, env)
    if isinstance(condition, str) or len(condition.terms) != 1 or condition.constant:
        raise CompileError(
            f"the condition on '{c.name}' is not a single decision", where=key,
        )
    (switch, coefficient), = condition.terms.items()
    column = columns.get(switch)
    if coefficient != 1.0 or column is None or column.kind != "binary":
        raise CompileError(
            f"'{c.name}' waits on '{switch}', which is not a binary decision",
            where=key,
        )
    if c.rel.op == "eq":
        raise CompileError(
            f"'{c.name}' is conditional and states an equality, which is two "
            "rules rather than one; write both halves, 'if ... then a <= b' "
            "and 'if ... then a >= b'",
            where=key,
        )

    # The row as the solver will see it: terms on the left, a number on the right.
    rhs = -body.constant
    lo, hi = _row_reach(body, columns)
    slack_needed = (None if hi is None else hi - rhs) if c.rel.op == "le" else (
        None if lo is None else rhs - lo
    )
    if slack_needed is None:
        raise CompileError(
            f"'{c.name}' applies only under a condition, so it needs to know how "
            "far it can reach when the condition does not hold, and the variables "
            "it is built from are not bounded enough to say; give them finite "
            "bounds, or state the rule without a condition",
            where=key,
        )
    reach = max(0.0, slack_needed)
    active = c.when_is == 1.0

    if c.rel.op == "le":
        # active:  T + M*y <= R + M      inactive at y=0, since T <= hi <= R + M
        # negated: T - M*y <= R          the same, read the other way round
        body.terms[switch] = body.terms.get(switch, 0.0) + (reach if active else -reach)
        return body, "le", rhs + (reach if active else 0.0)

    body.terms[switch] = body.terms.get(switch, 0.0) + (-reach if active else reach)
    return body, "ge", rhs - (reach if active else 0.0)


def _row_reach(body, columns: dict[str, FlatVar]) -> tuple[float | None, float | None]:
    """How low and how high this row's left-hand side can reach.

    Taken from the columns' own bounds. Used to cap the slack, which matters
    more than it sounds: an unbounded column is one CP-SAT will not accept, so
    leaving the cap off would cost a discrete model its engine the moment one
    of its rules became soft.
    """
    lo: float | None = 0.0
    hi: float | None = 0.0
    for key, coef in body.terms.items():
        column = columns.get(key)
        if column is None:
            return None, None
        ends = []
        for bound in (column.lb, column.ub):
            ends.append(None if bound is None or math.isinf(bound) else coef * bound)
        # An open end only takes away the side it is open towards, which depends
        # on the sign of the coefficient. Losing both would refuse rules that the
        # bounds do prove.
        if None in ends:
            open_low = (ends[0] is None) if coef >= 0 else (ends[1] is None)
            if open_low:
                lo = None
            else:
                hi = None
            known = next((e for e in ends if e is not None), None)
            if known is not None:
                if lo is not None:
                    lo += known
                if hi is not None:
                    hi += known
            else:
                lo = hi = None
            continue
        lo = None if lo is None else lo + min(*ends)
        hi = None if hi is None else hi + max(*ends)
    return lo, hi


def _violation_bound(
    role: str, reach: tuple[float | None, float | None], rhs: float
) -> float | None:
    lo, hi = reach
    if role == "over":
        return None if hi is None else max(0.0, hi - rhs)
    return None if lo is None else max(0.0, rhs - lo)


def _index_of(key: str, name: str) -> list[str]:
    inside = key[len(name) + 1 : -1] if key.startswith(f"{name}[") else ""
    return inside.split(",") if inside else []


def _flatten_objectives(
    model: IRModel, ev: Evaluator, penalties: dict[str, float] | None = None
) -> tuple[FlatObjective, list[dict]]:
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
    if penalties:
        # One component for every soft constraint together, rather than one per
        # family: the objectives panel says what breaking rules cost in total,
        # and which rules broke is a property of the rows, reported there.
        total = total + Quadratic(0.0, dict(penalties))
        components.append(
            {
                "name": "constraint_penalties",
                "sense": "minimize",
                "weight": 1.0,
                "unit": "penalty points",
                "terms": dict(penalties),
                "quadratic": [],
                "constant": 0.0,
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
