"""Trees, declared once.

A hierarchy used to reach a model as four tables the author typed out and kept
consistent by hand. The tables are still tables — that is a deliberate decision
about model size, recorded in DECISIONS.md — but they are now computed. These
tests hold the derivation against a tree checked by eye, and hold the construct
against the hand-written tables it replaced.
"""

from __future__ import annotations

import hashlib
import pathlib

import pytest

from psp.compiler import compile_and_flatten
from psp.dsl import ResolveError, parse_problem, write_problem
from psp.problem.hierarchy import Hierarchy, HierarchyError, derive

EXAMPLES = pathlib.Path(__file__).resolve().parents[2] / "examples"

UNITS = ["organisation", "operations", "training",
         "ops_north", "ops_south", "instruction", "assessment"]
TREE = Hierarchy(
    name="Units", covers="covers", overlap="overlap", leaf="leaf", count="count",
    depth="depth",
    parent={"operations": "organisation", "training": "organisation",
            "ops_north": "operations", "ops_south": "operations",
            "instruction": "training", "assessment": "training"},
)


def table(hierarchy: Hierarchy, elements: list[str], name: str) -> dict:
    found = next(t for t in derive(hierarchy, elements) if t["name"] == name)
    return {tuple(index): value for index, value in found["values"]}


def test_covers_is_ancestor_or_self_and_nothing_else():
    covers = table(TREE, UNITS, "covers")
    assert covers[("organisation", "ops_north")] == 1.0, "an ancestor two levels up"
    assert covers[("ops_north", "ops_north")] == 1.0, "a node covers itself"
    assert ("ops_north", "organisation") not in covers, "covering is not symmetric"
    assert ("operations", "instruction") not in covers, "cousins do not cover"
    # Seven nodes cover themselves; the root covers six others, and the two
    # branches two each.
    assert len(covers) == 7 + 6 + 2 + 2


def test_overlap_counts_the_leaves_two_nodes_share():
    overlap = table(TREE, UNITS, "overlap")
    assert overlap[("organisation", "organisation")] == 4.0
    assert overlap[("operations", "operations")] == 2.0
    assert overlap[("organisation", "operations")] == 2.0
    assert overlap[("ops_north", "ops_north")] == 1.0
    assert ("operations", "training") not in overlap, "sibling branches share nothing"
    assert ("operations", "instruction") not in overlap
    # Symmetric, which the hand-written table had to be by hand.
    for (a, b), shared in overlap.items():
        assert overlap[(b, a)] == shared


def test_leaves_are_the_nodes_with_nothing_beneath_them():
    assert set(table(TREE, UNITS, "leaf")) == {
        ("ops_north",), ("ops_south",), ("instruction",), ("assessment",)
    }
    assert table(TREE, UNITS, "count")[()] == 4.0


def test_depth_is_the_distance_from_the_root():
    depth = table(TREE, UNITS, "depth")
    assert ("organisation",) not in depth, "the root is at zero, which is the default"
    assert depth[("operations",)] == 1.0
    assert depth[("ops_north",)] == 2.0


@pytest.mark.parametrize(
    "parent,complaint",
    [
        ({"ops_north": "nowhere"}, "not an element"),
        ({"nowhere": "operations"}, "not an element"),
        ({"ops_north": "ops_north"}, "its own parent"),
        ({"a": "b", "b": "a"}, "cycle"),
    ],
)
def test_a_map_that_is_not_a_tree_is_refused_with_the_reason(parent, complaint):
    """Every one of these produces nonsense downstream rather than an error: a
    cycle makes covering non-transitive, an unknown node drops silently out of
    every table. They are caught while the author still has the map in view."""
    elements = UNITS + ["a", "b"]
    with pytest.raises(HierarchyError) as caught:
        derive(Hierarchy(name="Units", covers="covers", parent=parent), elements)
    assert complaint in str(caught.value)


def test_a_hierarchy_over_a_set_that_does_not_exist_is_refused():
    source = """
problem p "P"
  "x"
set Units = a, b of unit
hierarchy Unit by parent
  leaf is_leaf
  = { b: a }
var pick[Units] binary means "m"
constraint c "s"
  sum(pick[u] for u in Units) <= 1
minimize z "z" unit "u":
  sum(pick[u] for u in Units)
"""
    with pytest.raises(ResolveError) as caught:
        parse_problem(source)
    assert "not a declared set" in str(caught.value)


# ------------------------------------------------- the model it was built for


def read(name: str):
    return parse_problem((EXAMPLES / name).read_text())


def flat_hash(spec, scenario: str | None = None) -> str:
    flat = compile_and_flatten(spec, scenario).flat
    # Excluding metadata: it carries the IR fingerprint, which moves whenever a
    # description or the order of parameters changes. What has to be identical
    # is the system that gets solved.
    return hashlib.sha256(flat.model_dump_json(exclude={"metadata"}).encode()).hexdigest()


def test_the_commitment_plan_compiles_to_what_the_written_out_tables_compiled_to():
    """The acceptance test for the construct.

    These are the hashes of the flat model the hand-written `unit_covers`,
    `unit_overlap`, `is_leaf`, `leaf_count` and `specialty_covers` produced,
    for every scenario, taken before they were deleted. Columns, rows, right
    hand sides and objective: unchanged. The tree now says what the five tables
    used to say, and says it once.
    """
    expected = {
        None: "69af3d5f92675b4e",
        "surge": "08bdce88b8937459",
        "invest": "9ff6ba19f198528d",
        "assessment_stood_down": "d345039b6c51eab0",
        "serialised_organisation": "c24f69b76011501f",
    }
    spec = read("commitment_planning.psp")
    for scenario, digest in expected.items():
        assert flat_hash(spec, scenario)[:16] == digest, scenario


def test_the_commitment_plan_keeps_its_trees_rather_than_their_consequences():
    spec = read("commitment_planning.psp")
    assert {h.name for h in spec.hierarchies} == {"Units", "Specialties"}

    derived = {p.name: p.derived_from for p in spec.parameters if p.derived_from}
    assert derived == {
        "unit_covers": "Units", "unit_overlap": "Units",
        "is_leaf": "Units", "leaf_count": "Units",
        "specialty_covers": "Specialties",
    }
    # And the source no longer contains the tables it used to carry.
    source = (EXAMPLES / "commitment_planning.psp").read_text()
    assert "param unit_overlap" not in source
    assert "param unit_covers" not in source
    assert "param specialty_covers" not in source


def test_the_leaf_count_can_no_longer_drift_from_the_leaves():
    """There used to be a test asserting these agreed, because one was typed and
    the other was typed. Now they come from the same parent map."""
    spec = read("commitment_planning.psp")
    leaves = [v.index[0] for v in spec.parameter("is_leaf").values if v.value == 1]
    (count,) = spec.parameter("leaf_count").values
    assert count.value == len(leaves)


def test_a_hierarchy_is_written_back_as_a_tree_not_as_its_tables():
    spec = read("commitment_planning.psp")
    source = write_problem(spec)
    assert "hierarchy Units by parent" in source
    assert "param unit_overlap" not in source, "the writer emitted a derived table"
    again = parse_problem(source)
    assert compile_and_flatten(again).ir.fingerprint() == compile_and_flatten(spec).ir.fingerprint()


@pytest.mark.parametrize(
    "name", ["commitment_planning.psp", "balanced_workload.psp",
             "duty_roster.psp", "ward_cover.psp"]
)
def test_every_example_survives_being_written_and_read_back(name):
    """The gap that let a writer bug live.

    The round-trip tests covered the built-in templates, not the examples, so
    nothing noticed that a parameter named `weight` — a keyword — was written
    quoted and could not be read back as a subscript.
    """
    spec = read(name)
    again = parse_problem(write_problem(spec))
    assert (
        compile_and_flatten(again).ir.fingerprint()
        == compile_and_flatten(spec).ir.fingerprint()
    )
