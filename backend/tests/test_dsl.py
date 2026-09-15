"""The problem language.

The headline test is the round trip: every built-in template, written out as
source and read back, must compile to a byte-identical model. A language that
can express all six can express what the platform can solve — and it is the
only check that stays honest as templates grow, because nobody can eyeball a
nine-hundred-line model for fidelity.
"""

from __future__ import annotations

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
