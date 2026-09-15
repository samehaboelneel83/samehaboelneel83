"""The problem language.

The headline test is the round trip: every built-in template, written out as
source and read back, must compile to a byte-identical model. A language that
can express all six can express what the platform can solve — and it is the
only check that stays honest as templates grow, because nobody can eyeball a
nine-hundred-line model for fidelity.
"""

from __future__ import annotations

import pathlib

import pytest

from psp.compiler import compile_and_flatten
from psp.dsl import DslError, ParseError, ResolveError, parse_problem, write_problem
from psp.execution.runner import run_solver
from psp.problem.templates.registry import available, get
from psp.provenance import build_solution
from psp.solvers.base import SolveOptions, SolveStatus

OPTIONS = SolveOptions(time_limit_seconds=120.0)

TRANSPORT = """
problem fuel "Fuel distribution"
  "Ship fuel from depots to bases at least cost."

set Sources = depot_north, depot_south, port_terminal of location
set Dests = base_1, base_2, base_3, forward_post of location

param supply[Sources] unit "units" = {
  depot_north: 300, depot_south: 260, port_terminal: 400 }
param demand[Dests] unit "units" = {
  base_1: 220, base_2: 180, base_3: 300, forward_post: 120 }
param cost[Sources, Dests] unit "cost/unit" = {
  (depot_north, base_1): 4, (depot_north, base_2): 6,
  (depot_north, base_3): 9, (depot_north, forward_post): 12,
  (depot_south, base_1): 7, (depot_south, base_2): 3,
  (depot_south, base_3): 5, (depot_south, forward_post): 8,
  (port_terminal, base_1): 6, (port_terminal, base_2): 8,
  (port_terminal, base_3): 4, (port_terminal, forward_post): 5 }

var ship[Sources, Dests] continuous in [0, inf] means "How much to move on this lane"

constraint supply_limit "A source cannot ship more than it holds"
  category physical
  forall s in Sources:
    sum(ship[s, d] for d in Dests) <= supply[s]

constraint meet_demand "Every destination receives at least what it requires"
  because "Demand here is a requirement, not a preference"
  forall d in Dests:
    sum(ship[s, d] for s in Sources) >= demand[d]

minimize total_cost "Minimise total shipping cost" unit "cost units":
  sum(cost[s, d] * ship[s, d] for s in Sources, d in Dests)

assume linear_cost "Shipping cost is proportional to quantity"
  because "Opening a lane is assumed free"
  affects total_cost

scenario demand_surge "Demand up 30%"
  scale demand by 1.3
"""


@pytest.mark.parametrize("key", available())
def test_every_template_survives_a_round_trip(key):
    """Template -> source text -> model, with nothing lost."""
    template = get(key)
    original = template.build(template.example())

    text = write_problem(original)
    reparsed = parse_problem(text)

    before = compile_and_flatten(original)
    after = compile_and_flatten(reparsed)
    assert after.ir.fingerprint() == before.ir.fingerprint()
    assert after.flat.stats() == before.flat.stats()

    # The parts a fingerprint would not notice.
    assert [a.statement for a in reparsed.assumptions] == [
        a.statement for a in original.assumptions
    ]
    assert [s.key for s in reparsed.scenarios] == [s.key for s in original.scenarios]
    assert [c.category for c in reparsed.constraints] == [
        c.category for c in original.constraints
    ]
    assert [v.decision_meaning for v in reparsed.variables] == [
        v.decision_meaning for v in original.variables
    ]


@pytest.mark.parametrize("key", available())
def test_written_text_is_stable(key):
    """Writing, reading and writing again gives the same text, so a stored
    model does not churn every time it passes through."""
    template = get(key)
    once = write_problem(template.build(template.example()))
    twice = write_problem(parse_problem(once))
    assert twice == once


def test_a_problem_authored_as_text_solves():
    spec = parse_problem(TRANSPORT)
    assert spec.key == "fuel"
    assert spec.metadata["authored_in"] == "dsl"

    compiled = compile_and_flatten(spec)
    result, _ = run_solver(compiled.flat, options=OPTIONS)
    solution = build_solution(compiled, result)
    assert result.status == SolveStatus.OPTIMAL
    # The same optimum the Python template reaches for the same data.
    assert solution.objectives[0].value == pytest.approx(3240.0)


def test_scenarios_written_as_text_apply():
    spec = parse_problem(TRANSPORT)
    scenario = spec.scenario("demand_surge")
    # 'scale demand by 1.3' expands to every value the parameter declares.
    assert len(scenario.overrides) == 4
    assert all(o.scale == pytest.approx(1.3) for o in scenario.overrides)

    compiled = compile_and_flatten(spec, "demand_surge")
    assert compiled.record.applied_overrides
    result, _ = run_solver(compiled.flat, options=OPTIONS)
    # Demand up 30% outruns supply, which is a finding, not a failure.
    assert result.status == SolveStatus.INFEASIBLE


def test_integer_sets_and_labels_survive():
    spec = parse_problem("""
problem t "Timed"
set Slots : int = 0..3 labels { 0: "Mon 09:00", 1: "Mon 11:00" }
param gap = 1
var run[Slots] binary means "Run in this period"
constraint once "Exactly one period is used"
  sum(run[t] for t in Slots) == 1
constraint late "Nothing before the gap"
  forall t in Slots where t < gap:
    run[t] == 0
minimize spread "Prefer early periods":
  sum(t * run[t] for t in Slots)
""")
    slots = next(s for s in spec.sets if s.name == "Slots")
    assert slots.kind == "int"
    assert slots.elements == ["0", "1", "2", "3"]
    assert slots.labels["0"] == "Mon 09:00"

    compiled = compile_and_flatten(spec)
    result, _ = run_solver(compiled.flat, options=OPTIONS)
    solution = build_solution(compiled, result)
    assert result.status == SolveStatus.OPTIMAL
    # Period 0 is forbidden, so the earliest allowed is 1 — and it reads as a time.
    assert solution.decisions[0].key == "run[1]"
    assert solution.decisions[0].label == "run[Mon 11:00]"


def test_index_arithmetic_works_in_subscripts():
    spec = parse_problem("""
problem t "Shifted"
set T : int = 0..3
param d = 1
var y[T] binary
constraint link "A period can only run if the one before it did"
  forall t in T where t >= 1:
    y[t] <= y[t - d]
minimize total "Use as few as possible":
  sum(y[t] for t in T)
""")
    flat = compile_and_flatten(spec).flat
    assert [c.key for c in flat.constraints] == ["link[1]", "link[2]", "link[3]"]
    assert flat.constraints[0].terms == {"y[1]": 1.0, "y[0]": -1.0}


def test_errors_point_at_the_mistake():
    cases = [
        ('problem p\nset A = a\nvar v[A] binary\nconstraint c "s"\n'
         '  forall i in A: v[i] <= suply[i]\n',
         "not a declared parameter or variable", 5),
        ('problem p\nset A = a\nparam p2[A] = { a: 1 }\nvar v[A] binary\n'
         'constraint c "s"\n  forall i in A: v[i] <= p2\n',
         "needs a subscript", 6),
        ('problem p\nset A = a\nparam p2[A] = { zz: 1 }\nvar v[A] binary\n'
         'constraint c "s"\n  forall i in A: v[i] <= 1\n',
         "is not in set 'A'", 3),
        ('problem p\nset A = a\nvar v[A] binary\nconstraint c "s"\n'
         '  forall i in B: v[i] <= 1\n', "unknown set 'B'", 5),
        ('problem p\nset A = a\nvar v[A] binary\nvar v[A] binary\n',
         "declared twice", 4),
        ('problem p\nset A : int = a, b\nvar v[A] binary\n',
         "not a valid element", 2),
    ]
    for source, expected, line in cases:
        with pytest.raises(DslError) as caught:
            parse_problem(source)
        error = caught.value
        assert expected in error.message, error.message
        assert error.line == line, f"{expected}: line {error.line}, wanted {line}"
        assert error.excerpt(), "an error with no excerpt cannot be acted on"


def test_typos_suggest_the_right_name():
    with pytest.raises(ResolveError) as caught:
        parse_problem('problem p\nset Sources = a\nparam supply[Sources] = { a: 1 }\n'
                      'var v[Sources] binary\nconstraint c "s"\n'
                      '  forall i in Sources: v[i] <= supply2[i]\n')
    assert "supply" in (caught.value.hint or "")


def test_a_problem_with_nothing_to_decide_is_refused():
    with pytest.raises(ResolveError, match="no decision variables"):
        parse_problem('problem p\nset A = a\nparam x[A] = { a: 1 }\n')


def test_syntax_errors_name_what_was_expected():
    with pytest.raises(ParseError, match="needs a relation"):
        parse_problem('problem p\nset A = a\nvar v[A] binary\nconstraint c "s"\n  v[a] + 1\n')
    with pytest.raises(ParseError, match="needs a kind"):
        parse_problem('problem p\nset A = a\nvar v[A]\n')
    with pytest.raises(ParseError, match="has no statement"):
        parse_problem('problem p\nset A = a\nvar v[A] binary\nconstraint c\n  v[a] <= 1\n')


def test_comments_and_blank_lines_are_ignored():
    spec = parse_problem("""
# a leading comment
problem p "Commented"

set A = a, b   # trailing comment

var v[A] binary
constraint c "At most one"
  sum(v[i] for i in A) <= 1
minimize z "Nothing in particular":
  sum(v[i] for i in A)
""")
    assert [s.name for s in spec.sets] == ["A"]
    assert len(spec.constraints) == 1


def test_parentheses_are_preserved_where_they_matter():
    """Flattening a + b + c into one node must not also flatten (a + b) - c."""
    spec = parse_problem("""
problem p "Grouping"
set A = a
param c1[A] = { a: 2 }
var x[A] continuous
constraint k "Grouped"
  forall i in A:
    c1[i] * (x[i] + 1) <= 10
minimize z "z":
  sum(x[i] for i in A)
""")
    flat = compile_and_flatten(spec).flat
    # 2 * (x + 1) is 2x + 2, so the row is 2x <= 8 once the constant moves.
    row = flat.constraints[0]
    assert row.terms == {"x[a]": 2.0}
    assert row.rhs == pytest.approx(8.0)


@pytest.mark.parametrize("key", ["export-me", "with space", "set", "2024-plan"])
def test_awkward_names_survive_a_round_trip(key):
    """A name that cannot be written bare is quoted, and must read back."""
    source = parse_problem(f"""
problem {key!r} "Awkward"
set A = a
var v[A] binary
constraint c "At most one"
  sum(v[i] for i in A) <= 1
minimize z "z":
  sum(v[i] for i in A)
""".replace("'", '"'))
    assert source.key == key
    again = parse_problem(write_problem(source))
    assert again.key == key
    assert (
        compile_and_flatten(again).ir.fingerprint()
        == compile_and_flatten(source).ir.fingerprint()
    )


EXAMPLES = pathlib.Path(__file__).resolve().parents[2] / "examples"


def test_the_commitment_planning_example_is_a_working_model():
    """A hierarchical commitment plan, written entirely in the language.

    It is the demonstration that a class of problem this shape needs no Python:
    units compete for limited time, commitments are weighted, mandatory ones
    cannot be dropped, and load is balanced across units.
    """
    spec = parse_problem((EXAMPLES / "commitment_planning.psp").read_text())
    compiled = compile_and_flatten(spec)
    result, _ = run_solver(compiled.flat, options=OPTIONS)
    assert result.status == SolveStatus.OPTIMAL
    solution = build_solution(compiled, result)

    weight = {v.index[0]: v.value for v in spec.parameter("weight").values}
    mandatory = {v.index[0] for v in spec.parameter("mandatory").values if v.value == 1}
    met = {d.index[0] for d in solution.decisions if d.variable == "met"}

    # Mandatory commitments are hard: dropping one is not a trade-off available.
    assert mandatory <= met, sorted(mandatory - met)

    # The instance is deliberately too tight for everything, so something goes.
    dropped = set(weight) - met
    assert dropped, "nothing was dropped, so the weighting is never exercised"

    # What goes is the least valuable — but only among commitments that actually
    # compete. Two commitments assigned to different units are not a trade-off
    # against each other, so the ordering is checked per unit.
    assigned = {
        (v.index[0], v.index[1]) for v in spec.parameter("assigned").values if v.value == 1
    }
    units = {unit for _, unit in assigned}
    compared = 0
    for unit in units:
        rivals = {c for c, u in assigned if u == unit}
        kept = {c for c in rivals & met if c not in mandatory}
        lost = rivals & dropped
        if kept and lost:
            assert max(weight[c] for c in lost) <= min(weight[c] for c in kept), unit
            compared += 1
    assert compared, "no unit had both a kept and a dropped commitment to compare"

    coverage = next(o for o in solution.objectives if o.name == "commitment_coverage")
    assert coverage.sense == "maximize"
    assert coverage.value == pytest.approx(sum(weight[c] for c in met))


def test_the_commitment_example_rolls_time_up_through_its_tree():
    """The time tree decomposes: the horizon is its weeks, and a week is its
    days. Checked on every unit, including the branches — which is the half the
    unit-tree test does not cover."""
    spec = parse_problem((EXAMPLES / "commitment_planning.psp").read_text())
    compiled = compile_and_flatten(spec)
    result, _ = run_solver(compiled.flat, options=OPTIONS)
    assert result.status == SolveStatus.OPTIMAL
    solution = build_solution(compiled, result)

    first = {v.index[0]: v.value for v in spec.parameter("first_period").values}
    last = {v.index[0]: v.value for v in spec.parameter("last_period").values}
    load = {
        (d.index[0], d.index[1]): d.value
        for d in solution.decisions if d.variable == "load"
    }
    nodes = compiled.ir.set("TimeNodes").elements
    weeks = [n for n in nodes if n.startswith("week_")]
    assert weeks, "the example has no weeks, so there is no rollup to check"

    for unit in compiled.ir.set("Units").elements:
        assert load.get((unit, "horizon"), 0.0) == pytest.approx(
            sum(load.get((unit, w), 0.0) for w in weeks)
        ), unit
        for week in weeks:
            days = [
                n for n in nodes
                if n not in weeks and n != "horizon"
                and first[n] >= first[week] and last[n] <= last[week]
            ]
            assert days, week
            assert load.get((unit, week), 0.0) == pytest.approx(
                sum(load.get((unit, d), 0.0) for d in days)
            ), (unit, week)


def test_load_rolls_up_the_unit_tree_as_well_as_the_horizon():
    """One rule aggregates both trees at once: a unit overlaps its relatives, a
    time node covers its periods. A parent's reported load must be its children's,
    at every node of the horizon.

    Because a commitment may sit at any level, load is counted in unit-periods:
    a commitment run at a branch costs one period from every unit beneath it.
    That is the only measure in which a parent equals the sum of its children
    whatever level the work was booked at, so it is the measure checked here."""
    spec = parse_problem((EXAMPLES / "commitment_planning.psp").read_text())
    compiled = compile_and_flatten(spec)
    result, _ = run_solver(compiled.flat, options=OPTIONS)
    assert result.status == SolveStatus.OPTIMAL
    solution = build_solution(compiled, result)

    overlap = {
        tuple(v.index): v.value for v in spec.parameter("unit_overlap").values
    }
    first = {v.index[0]: v.value for v in spec.parameter("first_period").values}
    last = {v.index[0]: v.value for v in spec.parameter("last_period").values}
    allowance = spec.parameter("allowance")
    limit = {tuple(v.index): v.value for v in allowance.values}

    allocated = [
        (d.index[1], int(d.index[2]))
        for d in solution.decisions if d.variable == "allocate"
    ]
    load = {
        (d.index[0], d.index[1]): d.value
        for d in solution.decisions if d.variable == "load"
    }
    nodes = compiled.ir.set("TimeNodes").elements

    # Reported load agrees with the allocations, for interior units too.
    for unit in compiled.ir.set("Units").elements:
        for node in nodes:
            actual = sum(
                overlap.get((unit, u), 0) for u, p in allocated
                if first[node] <= p <= last[node]
            )
            assert load.get((unit, node), 0.0) == pytest.approx(actual), (unit, node)
            assert actual <= limit.get((unit, node), allowance.default) + 1e-6

    # A parent is exactly its children, everywhere in the horizon.
    families = {
        "organisation": ["operations", "training"],
        "operations": ["ops_north", "ops_south"],
        "training": ["instruction", "assessment"],
    }
    for parent, children in families.items():
        for node in nodes:
            assert load.get((parent, node), 0.0) == pytest.approx(
                sum(load.get((child, node), 0.0) for child in children)
            ), (parent, node)

    # And the branch totals are real work, not zeros that trivially agree.
    assert load[("organisation", "horizon")] > 0


def test_an_interior_unit_allowance_binds_on_its_children():
    """The reason to roll the unit tree up: a branch that cannot afford the sum
    of its children constrains them, even where each child would fit alone."""
    spec = parse_problem((EXAMPLES / "commitment_planning.psp").read_text())
    compiled = compile_and_flatten(spec)
    result, _ = run_solver(compiled.flat, options=OPTIONS)
    solution = build_solution(compiled, result)

    limit = {tuple(v.index): v.value for v in spec.parameter("allowance").values}
    leaves = {v.index[0] for v in spec.parameter("is_leaf").values if v.value == 1}
    load = {
        (d.index[0], d.index[1]): d.value
        for d in solution.decisions if d.variable == "load"
    }

    # The branch caps really are tighter than their children added together,
    # or the rollup would be reporting rather than constraining.
    for parent, children in (("operations", ["ops_north", "ops_south"]),
                             ("training", ["instruction", "assessment"])):
        assert limit[(parent, "horizon")] < sum(limit[(c, "horizon")] for c in children)

    binding = {
        outcome.index[0] for outcome in solution.binding_constraints
        if outcome.name == "allowance_at_every_level"
    }
    interior = binding - leaves
    assert interior, f"only leaf allowances bound: {sorted(binding)}"
    assert "organisation" in interior, "the root allowance never bound"
    assert load[("organisation", "horizon")] == pytest.approx(
        limit[("organisation", "horizon")]
    )


def test_a_parent_unit_blocks_its_children_through_the_tree():
    """Availability is declared where it belongs and travels along the path,
    rather than being copied onto every unit before solving.

    It has to travel both ways now that a commitment may sit anywhere in the
    tree: downwards, so a branch that is spoken for stops its units; and
    upwards, so one unit being unavailable stops a commitment booked above it,
    which would otherwise have needed that unit in the room."""
    spec = parse_problem((EXAMPLES / "commitment_planning.psp").read_text())
    overlap = {
        tuple(v.index): v.value for v in spec.parameter("unit_overlap").values
    }
    blocked = {
        (v.index[0], int(v.index[1]))
        for v in spec.parameter("available").values if v.value == 0
    }
    # The example declares this at both ends of the tree — on a branch, so the
    # block has somewhere to travel down to, and on a leaf, so it has somewhere
    # to travel up to. Either one alone would leave half of it untested.
    leaves = {v.index[0] for v in spec.parameter("is_leaf").values if v.value == 1}
    declared = {unit for unit, _ in blocked}
    assert declared & leaves and declared - leaves

    compiled = compile_and_flatten(spec)
    result, _ = run_solver(compiled.flat, options=OPTIONS)
    solution = build_solution(compiled, result)
    allocated = [
        (d.index[0], d.index[1], int(d.index[2]))
        for d in solution.decisions if d.variable == "allocate"
    ]

    for _, unit, period in allocated:
        for other, at in blocked:
            assert not (overlap.get((other, unit), 0) > 0 and at == period), (
                other, unit, period
            )

    # A sibling branch is untouched, so the block reached its own units and
    # stopped there rather than blocking the period outright.
    blocked_periods = {at for _, at in blocked}
    elsewhere = [a for a in allocated if a[2] in blocked_periods]
    assert elsewhere, "nothing ran in those periods, so nothing is demonstrated"

    # Upwards is the half that only appears once commitments may sit above the
    # leaves, and a negative assertion on the baseline does not demonstrate it —
    # so a scenario stands one leaf down and the work booked above it has to move.
    stood_down = compile_and_flatten(spec, "assessment_stood_down")
    result, _ = run_solver(stood_down.flat, options=OPTIONS)
    assert result.status == SolveStatus.OPTIMAL
    away = {
        int(o.index[1]) for o in spec.scenario("assessment_stood_down").overrides
        if o.parameter == "available" and o.index[0] == "assessment" and o.value == 0
    }
    assert away, "the scenario stands nobody down"

    moved = [
        (d.index[0], d.index[1], int(d.index[2]))
        for d in build_solution(stood_down, result).decisions
        if d.variable == "allocate"
    ]
    over_assessment = [
        a for a in moved if overlap.get((a[1], "assessment"), 0) > 0 and a[2] in away
    ]
    assert not over_assessment, over_assessment

    # Not vacuous: the periods themselves stayed open to everyone else, and work
    # really was booked above the unit that went away.
    assert [a for a in moved if a[2] in away], "the week emptied out entirely"
    assert [a for a in moved if a[1] not in leaves], "nothing was booked above a leaf"


def test_a_commitment_may_sit_at_any_level_and_engages_everything_beneath_it():
    """The plan books work at branches as well as leaves, and a branch booking
    is not a name on a page: every unit under it is in the room, so none of them
    can be doing anything else at that moment."""
    spec = parse_problem((EXAMPLES / "commitment_planning.psp").read_text())
    covers = {tuple(v.index) for v in spec.parameter("unit_covers").values if v.value == 1}
    overlap = {
        tuple(v.index): v.value for v in spec.parameter("unit_overlap").values
    }
    leaves = {v.index[0] for v in spec.parameter("is_leaf").values if v.value == 1}
    assigned = {
        (v.index[0], v.index[1]) for v in spec.parameter("assigned").values if v.value == 1
    }
    # The example assigns commitments above the leaves, or there is no level to
    # exercise; and at the leaves too, or every level is the same level.
    assert {u for _, u in assigned} - leaves
    assert {u for _, u in assigned} & leaves

    compiled = compile_and_flatten(spec)
    result, _ = run_solver(compiled.flat, options=OPTIONS)
    assert result.status == SolveStatus.OPTIMAL
    solution = build_solution(compiled, result)
    allocated = [
        (d.index[0], d.index[1], int(d.index[2]))
        for d in solution.decisions if d.variable == "allocate"
    ]
    load = {
        (d.index[0], d.index[1]): d.value
        for d in solution.decisions if d.variable == "load"
    }

    branch_work = [a for a in allocated if a[1] not in leaves]
    assert branch_work, "the plan booked nothing above a leaf"

    for commitment, branch, period in branch_work:
        beneath = [u for u in leaves if (branch, u) in covers]
        assert len(beneath) > 1, branch
        for unit in beneath:
            clash = [
                (c, u) for c, u, p in allocated
                if p == period and overlap.get((u, unit), 0) > 0
                and (c, u) != (commitment, branch)
            ]
            assert not clash, (unit, period, clash)

    # And it is paid for at every unit it engaged, not once at the branch.
    for commitment, branch, _ in branch_work:
        for unit in leaves:
            if (branch, unit) in covers:
                assert load[(unit, "horizon")] > 0, unit


def test_frequency_is_met_inside_each_node_not_merely_in_total():
    """Six patrols in one week and none in the next is not a fortnight of
    patrolling, which is the difference between a frequency and a total."""
    spec = parse_problem((EXAMPLES / "commitment_planning.psp").read_text())
    compiled = compile_and_flatten(spec)
    result, _ = run_solver(compiled.flat, options=OPTIONS)
    solution = build_solution(compiled, result)

    first = {v.index[0]: v.value for v in spec.parameter("first_period").values}
    last = {v.index[0]: v.value for v in spec.parameter("last_period").values}
    required = {tuple(v.index): v.value for v in spec.parameter("required_in").values}
    met = {d.index[0] for d in solution.decisions if d.variable == "met"}
    allocated = [
        (d.index[0], int(d.index[2]))
        for d in solution.decisions if d.variable == "allocate"
    ]

    assert required, "the example states no frequencies, so this proves nothing"
    for (commitment, node), needed in required.items():
        inside = sum(
            1 for c, p in allocated
            if c == commitment and first[node] <= p <= last[node]
        )
        assert inside == (needed if commitment in met else 0), (commitment, node)


def test_specialty_capacity_binds_at_the_parent_not_only_the_leaves():
    """The point of a specialty tree: crafts that each fit can still overrun the
    capability they share. A flat model would see no constraint at all."""
    spec = parse_problem((EXAMPLES / "commitment_planning.psp").read_text())

    def binding_nodes(scenario=None):
        compiled = compile_and_flatten(spec, scenario)
        result, _ = run_solver(compiled.flat, options=OPTIONS)
        assert result.status == SolveStatus.OPTIMAL, scenario
        solution = build_solution(compiled, result)
        counts: dict[str, int] = {}
        for outcome in solution.binding_constraints:
            if outcome.name == "specialty_capacity_at_every_level":
                counts[outcome.index[0]] = counts.get(outcome.index[0], 0) + 1
        return counts

    baseline = binding_nodes()
    # Interior nodes of the tree, not just the crafts at its leaves.
    assert baseline.get("capability", 0) >= 1
    assert {"field", "classroom"} & set(baseline)

    # Cutting the root alone bites hard, and it is the only thing cut: the
    # scenario leaves every craft capacity exactly where it was, so whatever
    # changes in the plan is the interior node doing the work.
    scenario = spec.scenario("serialised_organisation")
    assert {o.parameter for o in scenario.overrides} == {"specialty_capacity"}
    assert {tuple(o.index) for o in scenario.overrides} == {("capability",)}

    serialised = binding_nodes("serialised_organisation")
    assert serialised["capability"] > 2 * baseline["capability"]

    # And it costs commitments, which no craft capacity could have caused.
    weight = {v.index[0]: v.value for v in spec.parameter("weight").values}

    def coverage(name=None):
        compiled = compile_and_flatten(spec, name)
        result, _ = run_solver(compiled.flat, options=OPTIONS)
        solution = build_solution(compiled, result)
        met = {d.index[0] for d in solution.decisions if d.variable == "met"}
        return sum(weight[c] for c in met)

    assert coverage("serialised_organisation") < coverage()


def test_the_leaf_count_agrees_with_the_units_marked_as_leaves():
    """The balance objective needs a count the language cannot take, so the
    count is stated. This is what keeps it true."""
    spec = parse_problem((EXAMPLES / "commitment_planning.psp").read_text())
    leaves = [v.index[0] for v in spec.parameter("is_leaf").values if v.value == 1]
    assert leaf_count(spec) == len(leaves)


def leaf_count(spec) -> int:
    """The stated number of leaf units. A scalar parameter holds its value as
    the single entry indexed by nothing, not as a default."""
    (only,) = spec.parameter("leaf_count").values
    return int(only.value)


def test_balance_never_decides_which_commitments_are_covered():
    """The weights are a claim, and this is the claim.

    Balance became a squared quantity, which is on a wholly different scale from
    the range it replaced, so the weights had to be reset to keep coverage
    strictly ahead of it. Asserting the numbers the model happens to produce
    would not test that; asserting the inequality the weights were chosen from
    does. The cheapest commitment, at its objective weight, must be worth more
    than the entire span of the balance term.
    """
    spec = parse_problem((EXAMPLES / "commitment_planning.psp").read_text())
    coverage = next(o for o in spec.objectives if o.name == "commitment_coverage")
    spread = next(o for o in spec.objectives if o.name == "load_spread")

    weight = {v.index[0]: v.value for v in spec.parameter("weight").values}
    cheapest_commitment = min(weight.values()) * coverage.weight

    # The balance term is one square per leaf unit, each bounded by the largest
    # a leaf's load can be against the whole organisation's.
    allowance = {tuple(v.index): v.value for v in spec.parameter("allowance").values}
    leaves = [v.index[0] for v in spec.parameter("is_leaf").values if v.value == 1]
    count = leaf_count(spec)
    biggest_leaf = max(allowance[(u, "horizon")] for u in leaves)
    # Scenarios may raise the root allowance, so take the largest one stated.
    biggest_total = max(
        [allowance[("organisation", "horizon")]]
        + [
            o.value for s in spec.scenarios for o in s.overrides
            if o.parameter == "allowance" and o.index == ["organisation", "horizon"]
            and o.value is not None
        ]
    )
    widest = max(count * biggest_leaf, biggest_total)
    most_the_spread_can_be = count * widest * widest * spread.weight

    assert cheapest_commitment > most_the_spread_can_be, (
        f"a commitment worth {cheapest_commitment} can be traded for balance "
        f"worth up to {most_the_spread_can_be}"
    )


def test_switching_to_a_variance_left_every_scenario_covering_the_same_work():
    """The point of retuning rather than merely swapping the measure.

    These coverage figures are the ones the model produced when balance was a
    range. A squared measure that changed them would be a squared measure that
    had started deciding which commitments get dropped.
    """
    spec = parse_problem((EXAMPLES / "commitment_planning.psp").read_text())
    weight = {v.index[0]: v.value for v in spec.parameter("weight").values}
    before = {
        None: 495.0, "surge": 410.0, "invest": 555.0,
        "assessment_stood_down": 495.0, "serialised_organisation": 465.0,
    }
    for scenario, coverage in before.items():
        compiled = compile_and_flatten(spec, scenario)
        result, _ = run_solver(compiled.flat, options=OPTIONS)
        assert result.status == SolveStatus.OPTIMAL, scenario
        solution = build_solution(compiled, result)
        met = {d.index[0] for d in solution.decisions if d.variable == "met"}
        assert sum(weight[c] for c in met) == pytest.approx(coverage), scenario


def test_the_balance_measure_is_a_variance_and_the_plan_is_balanced():
    """A variance sees the middle of the distribution, which is the whole reason
    for the change — so the plan it produces is checked for being level, not
    merely for having matching extremes."""
    spec = parse_problem((EXAMPLES / "commitment_planning.psp").read_text())
    compiled = compile_and_flatten(spec)
    assert compiled.flat.objective.is_quadratic
    assert compiled.flat.objective.convex is True

    result, meta = run_solver(compiled.flat, options=OPTIONS)
    assert result.status == SolveStatus.OPTIMAL
    # Discrete throughout, which is what lets the quadratic reach an engine here.
    assert meta["solver"] == "cpsat"
    solution = build_solution(compiled, result)

    load = {
        (d.index[0], d.index[1]): d.value
        for d in solution.decisions if d.variable == "load"
    }
    leaves = [v.index[0] for v in spec.parameter("is_leaf").values if v.value == 1]
    loads = sorted(load.get((u, "horizon"), 0.0) for u in leaves)

    # The two units that can take the same work carry the same amount of it.
    assert load[("ops_north", "horizon")] == load[("ops_south", "horizon")]

    # And the reported spread is the sum of squared deviations the model states,
    # recomputed here from the plan rather than read back from the solver.
    count = leaf_count(spec)
    total = sum(loads)
    expected = sum((count * x - total) ** 2 for x in loads)
    spread = next(o for o in solution.objectives if o.name == "load_spread")
    assert spread.sense == "minimize"
    assert spread.value == pytest.approx(expected)
    assert spread.unit == "squared unit-periods"


def test_the_commitment_example_responds_to_its_scenarios():
    spec = parse_problem((EXAMPLES / "commitment_planning.psp").read_text())
    weight = {v.index[0]: v.value for v in spec.parameter("weight").values}

    def coverage(scenario=None):
        compiled = compile_and_flatten(spec, scenario)
        result, _ = run_solver(compiled.flat, options=OPTIONS)
        assert result.status == SolveStatus.OPTIMAL, scenario
        solution = build_solution(compiled, result)
        met = {d.index[0] for d in solution.decisions if d.variable == "met"}
        return sum(weight[c] for c in met), met

    baseline, baseline_met = coverage()
    cut, cut_met = coverage("surge")
    invested, invested_met = coverage("invest")

    # Less capacity covers less; more covers more. Neither is guaranteed by the
    # model, so both are worth asserting.
    assert cut < baseline < invested
    assert cut_met < baseline_met < invested_met
    assert invested == pytest.approx(sum(weight.values())), "investing should cover everything"


def test_scaling_a_parameter_with_no_values_is_refused():
    """A scenario that silently changes nothing is worse than one that fails:
    it reports 'no difference' and nobody looks again."""
    with pytest.raises(ResolveError, match="nothing for this scenario to change"):
        parse_problem("""
problem p "P"
set A = a
param cap[A] default 5
var v[A] binary
constraint c "At most one"
  sum(v[i] for i in A) <= 1
minimize z "z":
  sum(v[i] for i in A)
scenario tighter "Half the capacity"
  scale cap by 0.5
""")
