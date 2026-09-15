"""Soft constraints.

A rule is hard unless someone says what breaking it costs. These tests hold
that default, the arithmetic of the exception, and the reporting — because a
platform that quietly satisfies a rule it actually broke is worse than one that
cannot bend at all.
"""

from __future__ import annotations

import pathlib

import pytest

from psp.compiler import compile_and_flatten
from psp.compiler.errors import CompileError
from psp.dsl import ResolveError, parse_problem, write_problem
from psp.execution.runner import run_solver
from psp.provenance import build_solution
from psp.solvers.base import SolveOptions, SolveStatus

OPTIONS = SolveOptions(time_limit_seconds=60.0)
EXAMPLES = pathlib.Path(__file__).resolve().parents[2] / "examples"

HEAD = """
problem tiny "Tiny"
  "One rule, stated three ways."
set Things = a, b of thing
var take[Things] binary means "Take this thing"
"""


def build(body: str, scenario: str | None = None):
    return compile_and_flatten(parse_problem(HEAD + body), scenario)


def roster():
    return parse_problem((EXAMPLES / "duty_roster.psp").read_text())


def outcome(solution, name: str, index: list[str]):
    everything = solution.binding_constraints + solution.slack_constraints
    return next(o for o in everything if o.name == name and list(o.index) == index)


def test_a_rule_is_hard_unless_a_price_is_given():
    """The default matters more than the feature. A constraint with no penalty
    gets no room to be broken, and an impossible one stays impossible."""
    compiled = build('''
constraint take_nothing "Take nothing at all"
  sum(take[t] for t in Things) == 0
constraint take_something "Take at least one thing"
  sum(take[t] for t in Things) >= 1
''')
    assert [v for v in compiled.flat.variables if v.role == "violation"] == []
    assert all(c.penalty is None for c in compiled.flat.constraints)
    result, _ = run_solver(compiled.flat, options=OPTIONS)
    assert result.status == SolveStatus.INFEASIBLE


@pytest.mark.parametrize(
    "op,expected",
    [("<=", ["~over"]), (">=", ["~under"]), ("==", ["~over", "~under"])],
)
def test_each_shape_of_row_gets_the_room_it_can_actually_use(op, expected):
    """A ``<=`` row can only be broken upwards and a ``>=`` row only downwards.
    Giving either the other direction would be a column that can never leave
    zero, and an equality genuinely needs both."""
    compiled = build(f'''
constraint limit "A limit"
  soft penalty 4
  sum(take[t] for t in Things) {op} 1
''')
    names = sorted({v.name for v in compiled.flat.variables if v.role == "violation"})
    assert names == sorted(expected)


def test_the_slack_is_integral_and_bounded_so_a_discrete_model_keeps_its_engine():
    """The trap this avoids: a continuous, unbounded slack turns a discrete
    model into a mixed one with an unbounded column, which CP-SAT refuses. A
    rule becoming soft would then silently cost the model its engine."""
    compiled = compile_and_flatten(roster())
    slack = [v for v in compiled.flat.variables if v.role == "violation"]
    assert slack, "the roster has no soft constraints, so this proves nothing"
    assert all(v.kind == "integer" for v in slack)
    assert all(v.ub is not None for v in slack)
    assert compiled.flat.stats()["variable_kinds"].get("continuous", 0) == 0

    _, meta = run_solver(compiled.flat, options=OPTIONS)
    assert meta["solver"] == "cpsat"


def test_the_bound_on_the_slack_is_what_the_row_can_actually_reach():
    """Three binary terms summing to zero can be missed by at most three, and
    cannot be missed downwards at all."""
    compiled = compile_and_flatten(roster())
    slack = {v.name + str(v.index): v for v in compiled.flat.variables if v.role == "violation"}
    over = slack["~over['honour_days_off']"]
    under = slack["~under['honour_days_off']"]
    assert over.ub == 3.0, "three requests on the same day can all be overridden"
    assert under.ub == 0.0, "a sum of binaries cannot fall below zero"


def test_a_violation_is_reported_rather_than_absorbed():
    compiled = compile_and_flatten(roster())
    result, _ = run_solver(compiled.flat, options=OPTIONS)
    assert result.status == SolveStatus.OPTIMAL
    solution = build_solution(compiled, result)

    broken = {(v.name, tuple(v.index)): v for v in solution.violations}
    assert ("honour_days_off", ()) in broken
    request = broken[("honour_days_off", ())]
    assert request.amount == pytest.approx(1.0)
    assert request.direction == "over"
    assert request.penalty == 20.0
    assert request.cost == pytest.approx(20.0)
    assert request.statement, "a violation with no statement explains nothing"

    # Dearest first, so the reader meets the expensive break before the cheap one.
    assert [v.cost for v in solution.violations] == sorted(
        (v.cost for v in solution.violations), reverse=True
    )


def test_room_to_break_a_rule_is_not_a_decision():
    compiled = compile_and_flatten(roster())
    result, _ = run_solver(compiled.flat, options=OPTIONS)
    solution = build_solution(compiled, result)
    assert solution.violations, "nothing broke, so the filter is untested"
    assert all(d.variable == "on_duty" for d in solution.decisions)
    assert not [d for d in solution.decisions if d.key.startswith("~")]


def test_the_reported_activity_is_the_rule_as_stated_not_as_made_feasible():
    """A soft row carries the slack that let it pass. Reporting that as the
    activity would show every broken rule sitting exactly on its limit."""
    compiled = compile_and_flatten(roster())
    result, _ = run_solver(compiled.flat, options=OPTIONS)
    solution = build_solution(compiled, result)
    row = outcome(solution, "honour_days_off", [])
    assert row.rhs == 0.0
    assert row.activity == pytest.approx(1.0), "the slack was counted into the activity"


def test_the_price_of_breaking_rules_is_an_objective_component():
    compiled = compile_and_flatten(roster())
    result, _ = run_solver(compiled.flat, options=OPTIONS)
    solution = build_solution(compiled, result)
    penalties = next(o for o in solution.objectives if o.name == "constraint_penalties")
    assert penalties.sense == "minimize"
    assert penalties.unit == "penalty points"
    assert penalties.value == pytest.approx(sum(v.cost for v in solution.violations))


def test_the_roster_costs_what_it_costs_on_paper():
    """Worked by hand before it was run.

    Baseline: five days over three people, each capped at two, so the shape is
    2-2-1 and two people get a second day — spread is broken twice, at 6 each.
    All three asked Monday off and somebody has to work it, so a request breaks
    once, at 20. Nothing else gives. 12 + 20 = 32.

    Short-handed: two people, capped at two, cover four of the five days. The
    uncovered day costs 50 whichever it is, so the model picks the one that also
    saves a request — Monday. Spread breaks twice again. 50 + 12 = 62.
    """
    spec = roster()
    for scenario, expected in ((None, 32.0), ("short_handed", 62.0)):
        compiled = compile_and_flatten(spec, scenario)
        result, _ = run_solver(compiled.flat, options=OPTIONS)
        assert result.status == SolveStatus.OPTIMAL, scenario
        assert result.objective_value == pytest.approx(expected), scenario


def test_the_cheapest_rule_to_break_is_the_one_that_breaks():
    """The reason penalties are numbers and not flags.

    Short-handed, a day must go uncovered. Every choice costs the same 50 except
    Monday, which also saves the 20 the request would have cost. The plan has to
    take Monday, and it has to still cover the rest.
    """
    compiled = compile_and_flatten(roster(), "short_handed")
    result, _ = run_solver(compiled.flat, options=OPTIONS)
    solution = build_solution(compiled, result)

    uncovered = [v for v in solution.violations if v.name == "every_day_is_covered"]
    assert len(uncovered) == 1, [v.index for v in uncovered]
    assert uncovered[0].index == ["mon"]
    assert uncovered[0].direction == "under"

    # And the request it was protecting is now kept.
    assert not [v for v in solution.violations if v.name == "honour_days_off"]


def test_a_hard_rule_still_cannot_be_bought():
    """Availability is hard in the roster, and the short-handed scenario is
    exactly the pressure that would tempt a model to break it."""
    spec = roster()
    compiled = compile_and_flatten(spec, "short_handed")
    result, _ = run_solver(compiled.flat, options=OPTIONS)
    solution = build_solution(compiled, result)
    assert not [d for d in solution.decisions if d.index[0] == "cleo"]
    assert not [v for v in solution.violations if v.name == "only_when_available"]


def test_a_penalty_that_makes_a_rule_free_is_refused():
    for price in ("0", "-5"):
        with pytest.raises(ResolveError) as caught:
            build(f'''
constraint limit "A limit"
  soft penalty {price}
  sum(take[t] for t in Things) <= 1
''')
        assert "free to break" in str(caught.value)


def test_a_soft_constraint_round_trips_through_the_language():
    spec = roster()
    again = parse_problem(write_problem(spec))
    assert [c.penalty for c in again.constraints] == [c.penalty for c in spec.constraints]
    assert (
        compile_and_flatten(again).ir.fingerprint()
        == compile_and_flatten(spec).ir.fingerprint()
    )


def test_a_price_can_be_a_parameter_so_a_scenario_can_move_it():
    """The question this makes askable: "what if keeping requests mattered more?"

    While a price was a literal it could only be answered by editing the model,
    which loses the comparison — the whole point of a scenario. Worked by hand:
    at sixty per override, covering Monday costs 60 plus the two second days
    (12) for 72, while leaving Monday unstaffed costs 50 plus one second day
    (6) for 56. So the roster gives up the day rather than the request.
    """
    spec = roster()
    assert spec.parameter("cost_of_an_override").values[0].value == 20.0

    base = compile_and_flatten(spec)
    reprice = compile_and_flatten(spec, "requests_are_sacred")
    assert run_solver(base.flat, options=OPTIONS)[0].objective_value == pytest.approx(32.0)

    result, _ = run_solver(reprice.flat, options=OPTIONS)
    assert result.status == SolveStatus.OPTIMAL
    assert result.objective_value == pytest.approx(56.0)

    solution = build_solution(reprice, result)
    broken = {v.name for v in solution.violations}
    assert "every_day_is_covered" in broken, "the day was covered at the higher price"
    assert "honour_days_off" not in broken, "a request was overridden anyway"

    # The rule carries the price the scenario gave it, not the one in the file.
    override = next(
        c for c in reprice.flat.constraints if c.name == "honour_days_off"
    )
    assert override.penalty == pytest.approx(60.0)


def test_a_price_that_is_not_a_fixed_number_is_refused():
    """A penalty has to be a number by the time a row exists. One that depends
    on a decision would make the objective quadratic in a way nobody wrote."""
    source = '''
problem p "P"
  "A price that is not known."
set Things = a, b of thing
var take[Things] binary means "Take it"
constraint limit "A limit"
  soft penalty take[a]
  sum(take[t] for t in Things) <= 1
minimize z "z" unit "u":
  sum(take[t] for t in Things)
'''
    with pytest.raises(CompileError) as caught:
        compile_and_flatten(parse_problem(source))
    # Caught where constants are evaluated, which says it more plainly than the
    # penalty-specific check behind it does.
    assert "used where a constant is required" in str(caught.value)
