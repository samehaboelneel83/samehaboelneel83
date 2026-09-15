"""Filling a problem in from the domain.

Eleven tables in the schema had no code touching them, and a problem that was
"about" the organisation's units still had to list those units again in its own
text. Two copies of the same facts, one of which gets updated.

These tests hold the seam: what a bound problem takes from the domain, that it
means exactly what the written-out version meant, and that every way of asking
the domain for something it does not have is refused by name.
"""

from __future__ import annotations

import pytest

from psp.compiler import compile_and_flatten
from psp.dsl import parse_problem, write_problem
from psp.problem.binding import (
    BindingError, DomainEntity, DomainSnapshot, bind, needs_binding,
)

SOURCE = """
problem crews "Crew capacity"
  "Sets, a tree and numbers, all taken from the domain."

set Units from unit

hierarchy Units by parent
  covers unit_covers
  leaf is_leaf
  count leaf_count
  from reports_to

param capacity[Units] from attribute capacity

var use[Units] continuous in [0, 100] means "Hours used at this unit"

constraint within_capacity "No unit is worked past its capacity"
  category physical
  forall u in Units:
    use[u] <= capacity[u]

constraint leaves_carry_the_work "Only the units with nothing beneath them work"
  category modelling
  never use[u] for u in Units where is_leaf[u] == 0

maximize worked "Hours worked" unit "hours":
  sum(use[u] for u in Units)
"""

WRITTEN_OUT = """
problem crews "Crew capacity"
  "The same problem with the domain typed out again."

set Units = hq, north, south of unit

hierarchy Units by parent
  covers unit_covers
  leaf is_leaf
  count leaf_count
  = { north: hq, south: hq }

param capacity[Units] = { hq: 0, north: 30, south: 25 }

var use[Units] continuous in [0, 100] means "Hours used at this unit"

constraint within_capacity "No unit is worked past its capacity"
  category physical
  forall u in Units:
    use[u] <= capacity[u]

constraint leaves_carry_the_work "Only the units with nothing beneath them work"
  category modelling
  never use[u] for u in Units where is_leaf[u] == 0

maximize worked "Hours worked" unit "hours":
  sum(use[u] for u in Units)
"""


def domain(**changes) -> DomainSnapshot:
    snapshot = DomainSnapshot(
        entities={
            "unit": [
                DomainEntity(key="hq", name="Headquarters", attributes={"capacity": 0}),
                DomainEntity(key="north", name="North", attributes={"capacity": 30}),
                DomainEntity(key="south", name="South", attributes={"capacity": 25}),
            ]
        },
        relationships={"reports_to": [("north", "hq"), ("south", "hq")]},
    )
    for field, value in changes.items():
        setattr(snapshot, field, value)
    return snapshot


def bound(snapshot: DomainSnapshot | None = None):
    return bind(parse_problem(SOURCE), snapshot or domain())


def test_a_bound_problem_is_the_problem_that_was_typed_out():
    """The acceptance test. Taking the elements, the tree and the numbers from
    the domain has to produce the same system as writing them in the file —
    otherwise the seam is a second way of saying something slightly different.
    """
    from_domain = compile_and_flatten(bound()).flat
    from_text = compile_and_flatten(parse_problem(WRITTEN_OUT)).flat
    assert from_domain.signature() == from_text.signature()


def test_the_problem_says_what_it_needs_before_anyone_supplies_it():
    """A problem that binds still parses, compiles its rules and resolves the
    names of tables it has not received yet. Nothing here needs a database."""
    spec = parse_problem(SOURCE)
    assert needs_binding(spec)
    assert spec.sets[0].elements == [], "a set was populated before binding"
    assert {p.name for p in spec.parameters} == {"capacity", "unit_covers",
                                                 "is_leaf", "leaf_count"}
    assert not needs_binding(parse_problem(WRITTEN_OUT))


def test_binding_fills_the_set_the_tree_and_the_numbers():
    spec = bound()
    assert spec.sets[0].elements == ["hq", "north", "south"]
    assert spec.hierarchies[0].parent == {"north": "hq", "south": "hq"}
    assert {v.index[0]: v.value for v in spec.parameter("capacity").values} == {
        "hq": 0.0, "north": 30.0, "south": 25.0
    }
    # The tables that follow from the tree are derived, not asked for.
    assert {v.index[0] for v in spec.parameter("is_leaf").values} == {"north", "south"}
    (count,) = spec.parameter("leaf_count").values
    assert count.value == 2


def test_every_bound_value_says_where_it_came_from():
    """A number taken from the domain that cannot be traced back to the entity
    it came from would break the chain this platform exists to keep."""
    for value in bound().parameter("capacity").values:
        assert value.origin is not None
        assert value.origin.source == "domain"
        assert value.origin.note == f"Units.{value.index[0]}.capacity"


def test_an_unbound_problem_writes_out_what_it_asked_for():
    """Round-tripping a problem that has not been bound must keep the request,
    not the empty tables it is still waiting on."""
    spec = parse_problem(SOURCE)
    source = write_problem(spec)
    assert "set Units from unit" in source
    assert "from reports_to" in source
    assert "param capacity[Units] from attribute capacity" in source
    again = parse_problem(source)
    assert needs_binding(again)
    assert compile_and_flatten(bind(again, domain())).flat.signature() == (
        compile_and_flatten(bound()).flat.signature()
    )


def test_a_bound_problem_writes_out_what_it_was_given():
    """Once bound, the problem *is* the resolved one — that is the point of
    binding at save time — so it writes as the facts it now holds."""
    source = write_problem(bound())
    assert "set Units = hq, north, south" in source
    assert "from reports_to" not in source


@pytest.mark.parametrize(
    "snapshot,complaint",
    [
        (DomainSnapshot(entities={"team": []}), "no entity type 'unit'"),
        (DomainSnapshot(entities={"unit": []}), "has no entities"),
    ],
)
def test_a_missing_entity_type_is_refused_by_name(snapshot, complaint):
    with pytest.raises(BindingError) as caught:
        bind(parse_problem(SOURCE), snapshot)
    assert complaint in str(caught.value)


def test_a_missing_relationship_type_lists_what_the_domain_does_have():
    with pytest.raises(BindingError) as caught:
        bind(parse_problem(SOURCE), domain(relationships={"manages": []}))
    message = str(caught.value)
    assert "no relationship type 'reports_to'" in message
    assert "manages" in message, "the error does not say what is there instead"


def test_an_edge_that_leaves_the_set_is_refused_rather_than_dropped():
    """Dropping it would produce a tree missing exactly the links that were
    wrong, which is the hardest kind of error to notice."""
    with pytest.raises(BindingError) as caught:
        bind(parse_problem(SOURCE),
             domain(relationships={"reports_to": [("north", "elsewhere")]}))
    assert "does not contain both" in str(caught.value)


def test_a_second_parent_is_refused():
    with pytest.raises(BindingError) as caught:
        bind(parse_problem(SOURCE),
             domain(relationships={"reports_to": [("north", "hq"), ("north", "south")]}))
    assert "two parents" in str(caught.value)


def test_a_relationship_that_is_not_a_tree_is_refused_as_one():
    with pytest.raises(BindingError) as caught:
        bind(parse_problem(SOURCE),
             domain(relationships={"reports_to": [("hq", "north"), ("north", "hq")]}))
    assert "does not describe a tree" in str(caught.value)


def test_a_missing_attribute_is_refused_unless_there_is_a_default():
    missing = domain(entities={"unit": [
        DomainEntity(key="hq", attributes={"capacity": 0}),
        DomainEntity(key="north", attributes={}),
        DomainEntity(key="south", attributes={"capacity": 25}),
    ]})
    with pytest.raises(BindingError) as caught:
        bind(parse_problem(SOURCE), missing)
    assert "no 'capacity' attribute" in str(caught.value)

    with_default = SOURCE.replace(
        "param capacity[Units] from attribute capacity",
        "param capacity[Units] default 0 from attribute capacity",
    )
    spec = bind(parse_problem(with_default), missing)
    assert {v.index[0] for v in spec.parameter("capacity").values} == {"hq", "south"}


def test_an_attribute_that_is_not_a_number_says_what_it_found():
    wrong = domain(entities={"unit": [
        DomainEntity(key="hq", attributes={"capacity": 0}),
        DomainEntity(key="north", attributes={"capacity": "plenty"}),
        DomainEntity(key="south", attributes={"capacity": 25}),
    ]})
    with pytest.raises(BindingError) as caught:
        bind(parse_problem(SOURCE), wrong)
    assert "'plenty'" in str(caught.value)


def test_an_attribute_on_a_set_that_is_not_bound_is_refused():
    source = WRITTEN_OUT.replace(
        "param capacity[Units] = { hq: 0, north: 30, south: 25 }",
        "param capacity[Units] from attribute capacity",
    )
    with pytest.raises(BindingError) as caught:
        bind(parse_problem(source), domain())
    assert "not bound to an entity type" in str(caught.value)


# ------------------------------------------------------------ through the API


def test_a_problem_binds_against_the_stored_domain(client):
    """The whole seam, over HTTP: entity types, entities with attributes, a
    relationship type and its edges, then a problem that names them."""
    assert client.post("/api/domain/entity-types",
                       json={"key": "unit", "name": "Unit"}).status_code == 201
    for key, capacity in (("hq", 0), ("north", 30), ("south", 25)):
        assert client.post("/api/domain/entities", json={
            "entity_type": "unit", "key": key, "name": key.title(),
            "attributes": {"capacity": capacity},
        }).status_code == 201
    assert client.post("/api/domain/relationship-types",
                       json={"key": "reports_to", "name": "Reports to"}).status_code == 201
    for child in ("north", "south"):
        assert client.post("/api/domain/relationships", json={
            "relationship_type": "reports_to", "source": child, "target": "hq",
        }).status_code == 201

    listed = client.get("/api/domain/relationship-types").json()["relationship_types"]
    assert listed[0]["key"] == "reports_to" and listed[0]["relationships"] == 2

    checked = client.post("/api/dsl/check", json={"source": SOURCE})
    assert checked.status_code == 200, checked.text
    # Three units came from the domain, so the model has three columns.
    assert checked.json()["statistics"]["variables"] == 3

    created = client.post("/api/dsl/problems", json={"source": SOURCE})
    assert created.status_code == 201, created.text

    solved = client.post("/api/problems/crews/solve", json={}).json()
    assert solved["solution"]["status"] == "optimal"
    # Only the leaves may work, so the answer is north plus south.
    assert solved["solution"]["objectives"][0]["value"] == pytest.approx(55.0)

    # What was stored is the bound problem, not the request for one.
    source = client.get("/api/dsl/problems/crews").json()["source"]
    assert "set Units = hq, north, south" in source
    assert "from reports_to" not in source


def test_asking_the_domain_for_something_it_lacks_is_a_located_error(client):
    """A problem naming a type nobody has defined is the author's mistake, and
    it comes back the way a language error does — said plainly, with what the
    domain does have."""
    response = client.post("/api/dsl/check", json={
        "source": SOURCE.replace("set Units from unit", "set Units from squadron"),
    })
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["kind"] == "binding"
    assert "no entity type 'squadron'" in detail["message"]
