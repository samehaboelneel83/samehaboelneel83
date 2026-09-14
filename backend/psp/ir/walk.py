"""Structural walks over IR expressions.

Used by the explanation layer to answer "which parameters feed this
constraint?" without evaluating anything.
"""

from __future__ import annotations

from collections.abc import Iterator


def nodes(node) -> Iterator:
    """Depth-first traversal of an expression or predicate tree."""
    if node is None:
        return
    yield node
    for child in _children(node):
        yield from nodes(child)


def _children(node) -> list:
    out = []
    for attr in ("arg", "body", "lhs", "rhs"):
        child = getattr(node, attr, None)
        if child is not None:
            out.append(child)
    for attr in ("args", "index"):
        children = getattr(node, attr, None)
        if children:
            out.extend(children)
    where = getattr(node, "where", None)
    if where is not None:
        out.append(where)
    return out


def referenced_parameters(*roots) -> set[str]:
    return {n.name for root in roots for n in nodes(root) if getattr(n, "op", None) == "param"}


def referenced_variables(*roots) -> set[str]:
    return {n.name for root in roots for n in nodes(root) if getattr(n, "op", None) == "var"}


def referenced_sets(*roots) -> set[str]:
    found: set[str] = set()
    for root in roots:
        for n in nodes(root):
            for b in getattr(n, "over", None) or []:
                found.add(b.set)
    return found


def constraint_parameters(constraint) -> set[str]:
    return referenced_parameters(constraint.rel.lhs, constraint.rel.rhs, constraint.where)


def objective_parameters(objective) -> set[str]:
    return referenced_parameters(objective.expr)
