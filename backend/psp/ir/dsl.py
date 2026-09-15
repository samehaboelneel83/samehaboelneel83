"""A thin Python surface for building IR expressions.

Templates are the platform's most-read code — an analyst checking whether the
vehicle-routing template really says what they think it says should be able to
read it. These helpers exist so a constraint reads roughly like the sentence it
implements, while still producing exactly the same JSON an external editor
would.
"""

from __future__ import annotations

from psp.ir.expr import (
    Add,
    And,
    Binding,
    Cmp,
    Const,
    Div,
    IdxRef,
    Lit,
    Mul,
    Neg,
    Not,
    Or,
    ParamRef,
    Rel,
    Sub,
    Sum,
    VarRef,
)


def num(value: float) -> Const:
    return Const(value=float(value))


def lit(value: str) -> Lit:
    return Lit(value=str(value))


def i(name: str) -> IdxRef:
    """The element currently bound to index ``name``."""
    return IdxRef(name=name)


def p(name: str, *index) -> ParamRef:
    return ParamRef(name=name, index=[_coerce(x) for x in index])


def v(name: str, *index) -> VarRef:
    return VarRef(name=name, index=[_coerce(x) for x in index])


def add(*args) -> Add:
    return Add(args=[_coerce(a) for a in args])


def sub(*args) -> Sub:
    return Sub(args=[_coerce(a) for a in args])


def mul(*args) -> Mul:
    return Mul(args=[_coerce(a) for a in args])


def div(a, b) -> Div:
    return Div(args=[_coerce(a), _coerce(b)])


def neg(a) -> Neg:
    return Neg(arg=_coerce(a))


def over(**bindings: str) -> list[Binding]:
    """``over(i="Plants", j="Markets")`` -> the bindings for those indices."""
    return [Binding(index=k, set=s) for k, s in bindings.items()]


def total(body, where=None, **bindings: str) -> Sum:
    """``total(v("ship", i("p"), i("m")), p="Plants", m="Markets")``."""
    return Sum(over=over(**bindings), body=_coerce(body), where=where)


def le(lhs, rhs) -> Rel:
    return Rel(op="le", lhs=_coerce(lhs), rhs=_coerce(rhs))


def ge(lhs, rhs) -> Rel:
    return Rel(op="ge", lhs=_coerce(lhs), rhs=_coerce(rhs))


def eq(lhs, rhs) -> Rel:
    return Rel(op="eq", lhs=_coerce(lhs), rhs=_coerce(rhs))


def differ(a: str, b: str) -> Cmp:
    """Index ``a`` and index ``b`` are bound to different elements."""
    return Cmp(op="ne", lhs=i(a), rhs=i(b))


def same(a: str, b: str) -> Cmp:
    return Cmp(op="eq", lhs=i(a), rhs=i(b))


def is_not(index_name: str, element: str) -> Cmp:
    return Cmp(op="ne", lhs=i(index_name), rhs=lit(element))


def is_(index_name: str, element: str) -> Cmp:
    return Cmp(op="eq", lhs=i(index_name), rhs=lit(element))


def cmp(op: str, lhs, rhs) -> Cmp:
    return Cmp(op=op, lhs=_coerce(lhs), rhs=_coerce(rhs))


def all_of(*preds):
    """Conjunction. A single condition is returned as itself.

    Wrapping one predicate in a one-element ``And`` is noise: it reads the same,
    compiles the same, and only shows up as a spurious difference when a model
    is written out and read back."""
    return preds[0] if len(preds) == 1 else And(args=list(preds))


def any_of(*preds):
    """Disjunction. A single condition is returned as itself."""
    return preds[0] if len(preds) == 1 else Or(args=list(preds))


def not_(pred) -> Not:
    return Not(arg=pred)


def _coerce(x):
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return Const(value=float(x))
    if isinstance(x, str):
        return Lit(value=x)
    return x
