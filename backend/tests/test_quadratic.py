"""The quadratic objective path.

A product of two decisions is allowed in an objective and refused everywhere
else. These tests hold both halves of that: the answers a quadratic gives, and
the refusals — because a feature whose boundary is vague is worse than one that
is merely narrow, and every refusal here has to name what to do instead.
"""

from __future__ import annotations

import pathlib

import pytest

from psp.compiler import compile_and_flatten
from psp.compiler.errors import NonLinearError
from psp.dsl import parse_problem, write_problem
from psp.execution.runner import run_solver
from psp.provenance import build_solution
from psp.solvers.base import SolveOptions, SolveStatus
from psp.solvers.registry import UnsupportedModelError

OPTIONS = SolveOptions(time_limit_seconds=60.0)
EXAMPLES = pathlib.Path(__file__).resolve().parents[2] / "examples"

# min x^2 + y^2 + xy subject to x + y >= 4.
#
# Worked by hand: substituting y = 4 - x gives x^2 - 4x + 16, least at x = 2, so
# the answer is 12 at (2, 2). The value is deliberately one that distinguishes
# the two plausible readings of a cross-term coefficient — the other convention
# gives 16 — so this test fails if the Hessian is built with the wrong factor.
CROSS_TERM = """
problem q "Cross term"
  "Two crews, a shared cost, and a floor on the total."

set Crews = north, south of crew

var work[Crews] {kind} in [0, 10] means "Work given to this crew"

constraint enough_is_done "At least four units of work happen"
  sum(work[c] for c in Crews) >= 4

minimize cost "Squares plus their interaction" unit "work squared":
  sum(work[c] * work[c] for c in Crews) + work[north] * work[south]
"""


def compile_source(text: str, scenario: str | None = None):
    return compile_and_flatten(parse_problem(text), scenario)


def test_a_product_of_decisions_becomes_degree_two_terms():
    flat = compile_source(CROSS_TERM.replace("{kind}", "continuous")).flat
    terms = {(t.i, t.j): t.coef for t in flat.objective.quadratic}
    assert terms == {
        ("work[north]", "work[north]"): 1.0,
        ("work[south]", "work[south]"): 1.0,
        ("work[north]", "work[south]"): 1.0,
    }
    # x*y and y*x are the same term, held once, or the coefficient would mean
    # something different depending on how the model happened to be written.
    assert ("work[south]", "work[north]") not in terms
    assert flat.objective.convex is True
    assert flat.stats()["is_quadratic"] is True


def test_highs_minimises_a_convex_quadratic_to_the_hand_computed_answer():
    compiled = compile_source(CROSS_TERM.replace("{kind}", "continuous"))
    result, meta = run_solver(compiled.flat, options=OPTIONS)
    assert meta["solver"] == "highs"
    assert result.status == SolveStatus.OPTIMAL
    assert result.objective_value == pytest.approx(12.0, abs=1e-6)
    assert result.values["work[north]"] == pytest.approx(2.0, abs=1e-5)
    assert result.values["work[south]"] == pytest.approx(2.0, abs=1e-5)


def test_cpsat_takes_the_same_objective_when_the_model_is_discrete():
    """One objective, two mechanisms. HiGHS minimises the quadratic directly;
    CP-SAT builds a variable per product and searches. The model does not know
    which, and the answer is the same either way."""
    compiled = compile_source(CROSS_TERM.replace("{kind}", "integer"))
    result, meta = run_solver(compiled.flat, options=OPTIONS)
    assert meta["solver"] == "cpsat"
    assert result.status == SolveStatus.OPTIMAL
    assert result.objective_value == pytest.approx(12.0, abs=1e-6)

    # And HiGHS was not merely passed over — it was ruled out, with a reason.
    highs = next(c for c in meta["considered"] if c["solver"] == "highs")
    assert highs["eligible"] is False
    assert "mixed-integer quadratic" in highs["reason"]


def test_a_non_convex_objective_is_refused_before_anything_runs():
    """HiGHS answers a non-convex quadratic with a bare error and no diagnosis,
    so the compiler decides convexity while the objective still has a name."""
    source = CROSS_TERM.replace("{kind}", "continuous").replace(
        "minimize cost", "maximize cost"
    ).replace(">= 4", "<= 4")
    compiled = compile_source(source)
    assert compiled.flat.objective.convex is False

    with pytest.raises(UnsupportedModelError) as caught:
        run_solver(compiled.flat, options=OPTIONS)
    assert "convex" in str(caught.value)
    assert all(not c["eligible"] for c in caught.value.considered)


def test_a_quadratic_over_both_kinds_of_variable_says_what_would_work():
    """Neither engine covers a mixed continuous-and-discrete quadratic. The
    refusal is only useful if it names the two shapes that are covered."""
    source = """
problem mixed "Mixed"
  "One of each kind of variable."
var a continuous in [0, 10] means "A continuous quantity"
var b integer in [0, 10] means "A discrete quantity"
constraint enough "Together they reach four"
  a + b >= 4
minimize cost "Squares" unit "squared":
  a * a + b * b
"""
    compiled = compile_source(source)
    assert compiled.flat.objective.convex is True
    with pytest.raises(UnsupportedModelError) as caught:
        run_solver(compiled.flat, options=OPTIONS)
    message = str(caught.value)
    assert "fully continuous" in message and "fully discrete" in message


def test_a_quadratic_constraint_is_refused_and_named():
    source = """
problem bad "Quadratic row"
  "A product of decisions where a row cannot take one."
set Crews = north, south of crew
var work[Crews] continuous in [0, 10] means "Work"
constraint product_of_two_decisions "Their product is capped"
  work[north] * work[south] <= 3
minimize total "Total work" unit "work":
  sum(work[c] for c in Crews)
"""
    with pytest.raises(NonLinearError) as caught:
        compile_source(source)
    message = str(caught.value)
    assert "objective" in message, "the error does not say where a product is allowed"
    assert "product_of_two_decisions" in message, "the error does not name the row"


def test_degree_three_is_refused_in_an_objective_too():
    source = CROSS_TERM.replace("{kind}", "continuous").replace(
        "sum(work[c] * work[c] for c in Crews) + work[north] * work[south]",
        "work[north] * work[north] * work[south]",
    )
    with pytest.raises(NonLinearError) as caught:
        compile_source(source)
    assert "degree three" in str(caught.value)
    assert "cost" in str(caught.value)


def test_a_linear_model_is_untouched_by_the_quadratic_path():
    """The walk that folds objectives now carries a second map. A linear model
    must come out of it exactly as before, or every existing model moved."""
    source = CROSS_TERM.replace("{kind}", "continuous").replace(
        "sum(work[c] * work[c] for c in Crews) + work[north] * work[south]",
        "sum(work[c] for c in Crews)",
    )
    flat = compile_source(source).flat
    assert flat.objective.quadratic == []
    assert flat.objective.convex is None
    assert flat.stats()["is_quadratic"] is False
    assert "quadratic_terms" not in flat.stats()
    result, meta = run_solver(flat, options=OPTIONS)
    assert meta["solver"] == "highs"
    assert result.objective_value == pytest.approx(4.0)


def test_the_reported_objective_is_the_whole_quadratic_not_its_linear_part():
    """The provenance layer values each objective itself, in the sense the user
    stated it. Summing only the linear terms would report 0 for this one."""
    compiled = compile_source(CROSS_TERM.replace("{kind}", "continuous"))
    result, _ = run_solver(compiled.flat, options=OPTIONS)
    solution = build_solution(compiled, result)
    cost = next(o for o in solution.objectives if o.name == "cost")
    assert cost.sense == "minimize"
    assert cost.value == pytest.approx(12.0, abs=1e-5)
    assert cost.unit == "work squared"


# --------------------------------------------------------- the worked example


def test_the_balanced_workload_example_solves_to_hand_computed_answers():
    """Every number here was worked out on paper first.

    Baseline: 96 hours over four teams is 24 each, and every team can take 24,
    so the spread is zero. Charlie away: 91 hours over three, but delta caps at
    25, so alpha and bravo take 33 each — deviations 9, 9, -19, 1 about a mean
    of 24, which is 81 + 81 + 361 + 1. More work: 120 hours, mean 30, and the
    caps force 35, 35, 25, 25 — four deviations of 5, which is 100.
    """
    spec = parse_problem((EXAMPLES / "balanced_workload.psp").read_text())
    expected = {None: 0.0, "charlie_unavailable": 524.0, "more_work": 100.0}
    for scenario, value in expected.items():
        compiled = compile_and_flatten(spec, scenario)
        assert compiled.flat.objective.convex is True, scenario
        result, meta = run_solver(compiled.flat, options=OPTIONS)
        assert result.status == SolveStatus.OPTIMAL, scenario
        assert meta["solver"] == "highs", scenario
        assert result.objective_value == pytest.approx(value, abs=1e-4), scenario

        # The mean really is the mean, so the spread is measured about the plan
        # the model produced rather than about a number assumed in advance.
        loads = [v for k, v in result.values.items() if k.startswith("load[")]
        assert result.values["mean_load"] == pytest.approx(sum(loads) / 4, abs=1e-5), scenario


def test_the_balanced_workload_example_round_trips_through_the_language():
    spec = parse_problem((EXAMPLES / "balanced_workload.psp").read_text())
    again = parse_problem(write_problem(spec))
    assert (
        compile_and_flatten(again).ir.fingerprint()
        == compile_and_flatten(spec).ir.fingerprint()
    )


def test_a_range_and_a_variance_disagree_about_which_plan_is_balanced():
    """Why the example is worth having: the linear measure every other model in
    the repository uses cannot see the difference these two plans have."""
    even = [30.0, 30.0, 30.0, 10.0]
    lopsided = [30.0, 20.0, 20.0, 10.0]
    assert max(even) - min(even) == max(lopsided) - min(lopsided)

    def spread(loads):
        mean = sum(loads) / len(loads)
        return sum((x - mean) ** 2 for x in loads)

    assert spread(even) > spread(lopsided)
