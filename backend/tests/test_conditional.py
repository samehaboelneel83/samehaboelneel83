"""Rules that apply only under a condition.

A rule that switches off is a rule with a constant in it — large enough that
the off case is no restriction, small enough that the on case still bites.
Choosing it badly does not fail: too small and the model quietly means
something else, too large and it solves slowly and rounds badly. So the
constant is never invented here. It is read off the bounds of the variables the
rule is built from, and where those do not prove one the compiler refuses.
"""

from __future__ import annotations

import pathlib

import pytest

from psp.compiler import compile_and_flatten
from psp.compiler.errors import CompileError
from psp.dsl import parse_problem, write_problem
from psp.execution.runner import run_solver
from psp.provenance import build_solution
from psp.solvers.base import SolveOptions, SolveStatus

OPTIONS = SolveOptions(time_limit_seconds=60.0)
EXAMPLES = pathlib.Path(__file__).resolve().parents[2] / "examples"

HEAD = """
problem depot "Depot"
  "Open depots and ship from them."
set Depots = north, south of depot
param minimum[Depots] "Least a depot may ship once open" = { north: 40, south: 25 }
var open[Depots] binary means "Open this depot"
var ship[Depots] continuous in [0, 100] means "Units shipped from this depot"

constraint worth_opening "A depot that opens ships at least its minimum"
"""
TAIL = """
minimize cost "Units shipped" unit "units":
  sum(ship[d] for d in Depots)
"""


def model(body: str):
    return compile_and_flatten(parse_problem(HEAD + body + "\n" + TAIL))


def depot():
    return parse_problem((EXAMPLES / "depot_opening.psp").read_text())


def test_the_constant_is_the_one_an_expert_would_have_written():
    """The strongest check available: the rows the construct generates are the
    rows a modeller who knew the trick would write by hand, coefficient for
    coefficient."""
    derived = model("  forall d in Depots:\n    if open[d] then ship[d] >= minimum[d]")
    by_hand = model("  forall d in Depots:\n    ship[d] >= minimum[d] * open[d]")
    assert [r.model_dump() for r in derived.flat.constraints] == [
        r.model_dump() for r in by_hand.flat.constraints
    ]


def test_the_rule_binds_when_the_condition_holds_and_not_otherwise():
    compiled = model("  forall d in Depots:\n    if open[d] then ship[d] >= minimum[d]")
    row = {r.key: r for r in compiled.flat.constraints}["worth_opening[north]"]
    # north: ship - 40*open >= 0. Open and it must carry 40; shut and it says
    # ship >= 0, which is no restriction at all.
    assert row.op == "ge" and row.rhs == pytest.approx(0.0)
    assert row.terms["ship[north]"] == pytest.approx(1.0)
    assert row.terms["open[north]"] == pytest.approx(-40.0)


def test_if_not_switches_the_rule_the_other_way():
    compiled = model("  forall d in Depots:\n    if not open[d] then ship[d] <= 0")
    row = {r.key: r for r in compiled.flat.constraints}["worth_opening[north]"]
    # Shut, and nothing ships. Open, and the row must not restrict anything:
    # ship <= 100, which is the ceiling the variable already had.
    assert row.op == "le"
    assert row.terms["ship[north]"] == pytest.approx(1.0)
    assert row.terms["open[north]"] == pytest.approx(-100.0)
    assert row.rhs == pytest.approx(0.0)


UNBOUNDED = HEAD.replace(
    "var ship[Depots] continuous in [0, 100]",
    "var ship[Depots] continuous in [0, inf]",
)


def test_a_rule_whose_reach_is_not_bounded_is_refused_rather_than_guessed():
    """The decision this phase was waiting on. No bound, no constant, no rule —
    rather than a number nobody chose.

    A ``<=`` rule that switches off must not restrict anything when it is off,
    which takes a ceiling. Without one there is nothing to derive from.
    """
    with pytest.raises(CompileError) as caught:
        compile_and_flatten(parse_problem(
            UNBOUNDED + "  forall d in Depots:\n    if not open[d] then ship[d] <= 0\n" + TAIL
        ))
    message = str(caught.value)
    assert "not bounded enough" in message
    assert "worth_opening" in message


def test_only_the_side_the_rule_needs_has_to_be_bounded():
    """A ``>=`` rule relaxes downwards, so it needs the floor and not the
    ceiling. Giving up both sides whenever one is open would refuse rules the
    model does prove — which is a blanket answer where a precise one exists.
    """
    compiled = compile_and_flatten(parse_problem(
        UNBOUNDED + "  forall d in Depots:\n    if open[d] then ship[d] >= minimum[d]\n" + TAIL
    ))
    row = {r.key: r for r in compiled.flat.constraints}["worth_opening[north]"]
    assert row.terms["open[north]"] == pytest.approx(-40.0)


def test_a_conditional_equality_is_refused_and_says_what_to_write():
    with pytest.raises(CompileError) as caught:
        model("  forall d in Depots:\n    if open[d] then ship[d] == minimum[d]")
    assert "two rules rather than one" in str(caught.value)


def test_a_condition_that_is_not_a_single_binary_decision_is_refused():
    with pytest.raises(CompileError) as caught:
        model("  forall d in Depots:\n    if ship[d] then ship[d] >= minimum[d]")
    assert "not a binary decision" in str(caught.value)


def test_a_conditional_rule_is_written_back_as_one():
    """Unlike the counted forms, the condition is kept on the constraint, so the
    language writes the rule rather than its encoding."""
    spec = depot()
    source = write_problem(spec)
    assert "if open[d] then" in source
    again = parse_problem(source)
    assert (
        compile_and_flatten(again).ir.fingerprint()
        == compile_and_flatten(spec).ir.fingerprint()
    )


# ---------------------------------------------------------- the worked example


def solve(scenario: str | None = None):
    compiled = compile_and_flatten(depot(), scenario)
    result, _ = run_solver(compiled.flat, options=OPTIONS)
    assert result.status == SolveStatus.OPTIMAL, scenario
    return result, build_solution(compiled, result)


def test_the_depot_plan_costs_what_it_costs_on_paper():
    """Worked by hand first.

    Demand is 70 against two depots of 60, so both open: 100 + 80 fixed. The
    cheapest shipping sends a to north (2) and b, c to south (2 and 3), which
    is 60 + 50 + 45 = 155, and 335 in total — but that leaves north carrying
    30 against a minimum of 40. Moving ten units of b or c to north costs three
    more per unit either way, so the answer is 365.
    """
    result, _ = solve()
    assert result.objective_value == pytest.approx(365.0)
    result, _ = solve("ignore_minimums")
    assert result.objective_value == pytest.approx(335.0)


def test_the_conditional_rule_changes_the_plan_it_is_meant_to_change():
    """A rule that never binds proves nothing. This one moves ten units and
    exactly thirty of cost."""
    with_rule, strict = solve()
    without_rule, loose = solve("ignore_minimums")
    assert with_rule.objective_value - without_rule.objective_value == pytest.approx(30.0)

    def carried(solution, depot_key):
        return sum(
            d.value for d in solution.decisions
            if d.variable == "ship" and d.index[0] == depot_key
        )

    assert carried(loose, "north") == pytest.approx(30.0), "the rule was not needed"
    assert carried(strict, "north") == pytest.approx(40.0), "the rule did not bind"


def test_an_exclusive_choice_needs_no_new_syntax():
    """XOR was on the roadmap for this phase and is not here, because exactly
    one of a set of binaries is what `exactly 1 of` already says."""
    compiled = model("  exactly 1 of open[d] for d in Depots")
    (row,) = compiled.flat.constraints
    assert row.op == "eq" and row.rhs == pytest.approx(1.0)
    assert set(row.terms) == {"open[north]", "open[south]"}
