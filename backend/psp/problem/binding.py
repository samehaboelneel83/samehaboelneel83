"""Filling a problem in from the domain.

A problem states what it is about — units, commitments, the tree they sit in,
the numbers against each. Until now it also had to *contain* all of that, typed
out again beside the entities an organisation already keeps. Two copies of the
same facts, and only one of them gets updated.

A bound problem names the domain instead:

    set Units from unit
    hierarchy Units by parent from reports_to
    param capacity[Units] from attribute capacity

and the elements, the parent links and the numbers come from the entities.

This module is deliberately free of the database. It takes a snapshot — plain
data — so that binding can be tested against a domain written in four lines,
and so the rule "nothing below the problem layer knows about storage" holds
here too.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from psp.problem.hierarchy import HierarchyError, derive
from psp.problem.spec import ParameterValue, ProblemParameter, ProblemSpec, SourceRef


class DomainEntity(BaseModel):
    key: str
    name: str = ""
    attributes: dict = Field(default_factory=dict)


class DomainSnapshot(BaseModel):
    """What the domain holds, as the binder needs to see it."""

    entities: dict[str, list[DomainEntity]] = Field(default_factory=dict)
    """Entity type key -> its entities, in the order they should index a set."""
    relationships: dict[str, list[tuple[str, str]]] = Field(default_factory=dict)
    """Relationship type key -> (source key, target key) pairs."""

    def entity_type(self, key: str) -> list[DomainEntity]:
        if key not in self.entities:
            raise BindingError(
                f"the domain has no entity type '{key}'",
                known=sorted(self.entities),
            )
        return self.entities[key]

    def relationship(self, key: str) -> list[tuple[str, str]]:
        if key not in self.relationships:
            raise BindingError(
                f"the domain has no relationship type '{key}'",
                known=sorted(self.relationships),
            )
        return self.relationships[key]


class BindingError(ValueError):
    """A problem asked the domain for something it does not have."""

    def __init__(self, message: str, known: list[str] | None = None):
        self.known = known or []
        if known:
            message = f"{message}; it has {', '.join(known) if known else 'nothing'}"
        super().__init__(message)


def _order(spec: ProblemSpec, set_name: str):
    """Keep a bound tree's tables in the set's own element order."""
    positions = {
        element: n
        for s in spec.sets if s.name == set_name
        for n, element in enumerate(s.elements)
    }
    return lambda element: positions.get(element, 0)


def needs_binding(spec: ProblemSpec) -> bool:
    return bool(
        any(s.from_entity_type for s in spec.sets)
        or any(h.from_relationship for h in spec.hierarchies)
        or any(p.from_attribute for p in spec.parameters)
    )


def bind(spec: ProblemSpec, snapshot: DomainSnapshot, origin: str = "domain") -> ProblemSpec:
    """Return a copy of ``spec`` with everything it asked the domain for.

    A copy, because the stored problem should be the bound one — a problem that
    resolved its sets at solve time would answer a different question every time
    the organisation hired someone, with nothing in the run record to say so.
    Binding happens once, when the problem is saved, and what is stored is the
    result.
    """
    bound = spec.model_copy(deep=True)
    of_type: dict[str, str] = {}

    for problem_set in bound.sets:
        if not problem_set.from_entity_type:
            continue
        entities = snapshot.entity_type(problem_set.from_entity_type)
        problem_set.elements = [e.key for e in entities]
        problem_set.entity_type = problem_set.from_entity_type
        of_type[problem_set.name] = problem_set.from_entity_type
        if not problem_set.elements:
            raise BindingError(
                f"set '{problem_set.name}' binds to entity type "
                f"'{problem_set.from_entity_type}', which has no entities"
            )

    elements_of = {s.name: set(s.elements) for s in bound.sets}

    for hierarchy in bound.hierarchies:
        if not hierarchy.from_relationship:
            continue
        known = elements_of.get(hierarchy.name, set())
        parent: dict[str, str] = {}
        for child, ancestor in snapshot.relationship(hierarchy.from_relationship):
            if child not in known or ancestor not in known:
                # Silently dropping these would produce a tree missing exactly
                # the links that were wrong, which is the hardest kind to spot.
                raise BindingError(
                    f"relationship '{hierarchy.from_relationship}' links "
                    f"'{child}' to '{ancestor}', and set '{hierarchy.name}' does "
                    "not contain both"
                )
            if child in parent:
                raise BindingError(
                    f"'{child}' is given two parents by relationship "
                    f"'{hierarchy.from_relationship}'"
                )
            parent[child] = ancestor
        hierarchy.parent = parent

        # The tables follow from the tree exactly as they do for a written-out
        # one; the only difference is where the edges came from.
        try:
            tables = derive(hierarchy, sorted(known, key=_order(bound, hierarchy.name)))
        except HierarchyError as exc:
            raise BindingError(
                f"relationship '{hierarchy.from_relationship}' does not describe a "
                f"tree over '{hierarchy.name}': {exc}"
            ) from exc
        declared = {p.name: p for p in bound.parameters}
        for table in tables:
            parameter = declared.get(table["name"])
            if parameter is None:
                raise BindingError(
                    f"hierarchy '{hierarchy.name}' derives '{table['name']}', which "
                    "the problem never declared"
                )
            parameter.values = [
                ParameterValue(index=list(index), value=value,
                               origin=SourceRef(source=origin))
                for index, value in table["values"]
            ]

    attributes_of = {
        name: {e.key: e.attributes for e in snapshot.entity_type(kind)}
        for name, kind in of_type.items()
    }

    for parameter in bound.parameters:
        if not parameter.from_attribute:
            continue
        if len(parameter.index_sets) != 1:
            raise BindingError(
                f"parameter '{parameter.name}' takes its values from an attribute, "
                "so it is indexed by exactly one set of entities"
            )
        (set_name,) = parameter.index_sets
        if set_name not in attributes_of:
            raise BindingError(
                f"parameter '{parameter.name}' reads attribute "
                f"'{parameter.from_attribute}' from set '{set_name}', which is not "
                "bound to an entity type"
            )
        values: list[ParameterValue] = []
        for key, attributes in attributes_of[set_name].items():
            if parameter.from_attribute not in attributes:
                if parameter.default is not None:
                    continue
                raise BindingError(
                    f"'{key}' has no '{parameter.from_attribute}' attribute, and "
                    f"parameter '{parameter.name}' has no default to fall back on"
                )
            raw = attributes[parameter.from_attribute]
            try:
                value = float(raw)
            except (TypeError, ValueError):
                raise BindingError(
                    f"'{key}' has '{parameter.from_attribute}' = {raw!r}, which is "
                    f"not a number, and parameter '{parameter.name}' needs one"
                ) from None
            values.append(
                ParameterValue(
                    index=[key], value=value,
                    origin=SourceRef(
                        source=origin,
                        note=f"{set_name}.{key}.{parameter.from_attribute}",
                    ),
                )
            )
        parameter.values = values

    return bound
