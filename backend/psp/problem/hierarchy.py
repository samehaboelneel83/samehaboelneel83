"""Trees, declared once.

A hierarchy reaches a model as tables: ancestor-or-self, shared leaves, which
nodes are leaves, how many. They are all derivable from the parent links, and
`DECISIONS.md` explains why they are tables rather than expressions computed
inside every rule that needs them — a leaf loop inside each such rule is the
difference between a model that flattens in seconds and one that does not.

What that reasoning never justified was making the author write them out. The
tables are derived data; this is the derivation, so the tree is stated once and
the four tables that follow from it are computed rather than transcribed.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Hierarchy(BaseModel):
    """A tree over the elements of one set, and what to call what follows.

    The derived names are the author's, not the platform's. A unit tree and a
    specialty tree in the same model each need their own ``covers`` table, and
    only the author knows which word their domain uses.
    """

    name: str
    """The set the tree is over."""
    parent: dict[str, str] = Field(default_factory=dict)
    """Child element -> parent element. A root is simply absent."""
    covers: str | None = None
    """Name for the ancestor-or-self table: 1 when the first covers the second."""
    overlap: str | None = None
    """Name for the shared-leaf count between two nodes."""
    leaf: str | None = None
    """Name for the 1-when-nothing-is-beneath-it table."""
    count: str | None = None
    """Name for the scalar count of leaves."""
    depth: str | None = None
    """Name for the distance from the root."""


class HierarchyError(ValueError):
    """A parent map that is not a tree over the set it claims."""


def check(hierarchy: Hierarchy, elements: list[str]) -> None:
    """Refuse a parent map that is not a tree, naming what is wrong with it.

    Every failure here produces nonsense downstream rather than an error — a
    cycle makes ``covers`` non-transitive, an unknown element silently drops
    out of every table — so each is caught while the author still has the map
    in front of them.
    """
    known = set(elements)
    for child, parent in hierarchy.parent.items():
        if child not in known:
            raise HierarchyError(
                f"'{child}' is not an element of set '{hierarchy.name}'"
            )
        if parent not in known:
            raise HierarchyError(
                f"'{child}' is given the parent '{parent}', which is not an "
                f"element of set '{hierarchy.name}'"
            )
        if child == parent:
            raise HierarchyError(f"'{child}' is given as its own parent")

    for element in elements:
        seen = [element]
        walker = element
        while walker in hierarchy.parent:
            walker = hierarchy.parent[walker]
            if walker in seen:
                cycle = " -> ".join([*seen[seen.index(walker):], walker])
                raise HierarchyError(f"the parents of '{hierarchy.name}' cycle: {cycle}")
            seen.append(walker)

    roots = [e for e in elements if e not in hierarchy.parent]
    if not roots:
        raise HierarchyError(f"'{hierarchy.name}' has no root")


def ancestors_or_self(hierarchy: Hierarchy, element: str) -> list[str]:
    chain = [element]
    walker = element
    while walker in hierarchy.parent:
        walker = hierarchy.parent[walker]
        chain.append(walker)
    return chain


def leaves_under(hierarchy: Hierarchy, element: str, elements: list[str]) -> list[str]:
    """Leaf elements covered by ``element``, itself included when it is a leaf."""
    has_children = set(hierarchy.parent.values())
    return [
        e for e in elements
        if e not in has_children and element in ancestors_or_self(hierarchy, e)
    ]


def derive(hierarchy: Hierarchy, elements: list[str]) -> list[dict]:
    """The tables that follow from the tree.

    Returned as plain dictionaries rather than parameters so that this module
    stays free of the problem layer and can be unit-tested against a parent map
    alone. Only non-default entries are emitted, which is what keeps the tables
    the size the hand-written ones were.
    """
    check(hierarchy, elements)
    has_children = set(hierarchy.parent.values())
    leaves = [e for e in elements if e not in has_children]
    covered = {e: leaves_under(hierarchy, e, elements) for e in elements}

    tables: list[dict] = []
    if hierarchy.covers:
        tables.append({
            "name": hierarchy.covers,
            "index_sets": [hierarchy.name, hierarchy.name],
            "default": 0.0,
            "values": [
                ([a, b], 1.0)
                for b in elements
                for a in ancestors_or_self(hierarchy, b)
            ],
        })
    if hierarchy.overlap:
        tables.append({
            "name": hierarchy.overlap,
            "index_sets": [hierarchy.name, hierarchy.name],
            "default": 0.0,
            "values": [
                ([a, b], float(shared))
                for a in elements
                for b in elements
                if (shared := len(set(covered[a]) & set(covered[b]))) > 0
            ],
        })
    if hierarchy.leaf:
        tables.append({
            "name": hierarchy.leaf,
            "index_sets": [hierarchy.name],
            "default": 0.0,
            "values": [([e], 1.0) for e in leaves],
        })
    if hierarchy.count:
        tables.append({
            "name": hierarchy.count,
            "index_sets": [],
            "default": None,
            "values": [([], float(len(leaves)))],
        })
    if hierarchy.depth:
        tables.append({
            "name": hierarchy.depth,
            "index_sets": [hierarchy.name],
            "default": 0.0,
            "values": [
                ([e], float(len(ancestors_or_self(hierarchy, e)) - 1))
                for e in elements
                if len(ancestors_or_self(hierarchy, e)) > 1
            ],
        })
    return tables
