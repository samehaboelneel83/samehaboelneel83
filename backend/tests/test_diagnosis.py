"""Why there is no plan.

"Infeasible" is the one answer a solver gives that a planner cannot act on.
These tests hold the answer that replaces it: which rules are fighting, by how
much, and what would have to change — and, just as importantly, that finding
out costs the real model nothing.
"""

from __future__ import annotations

import pathlib

import pytest

from psp.compiler import compile_and_flatten
from psp.dsl import parse_problem
from psp.execution.diagnosis import diagnose
from psp.execution.runner import run_solver
from psp.solvers.base import SolveOptions, SolveStatus

OPTIONS = SolveOptions(time_limit_seconds=60.0)
EXAMPLES = pathlib.Path(__file__).resolve().parents[2] / "examples"


def ward(scenario: str | None = None):
    spec = parse_problem((EXAMPLES / "ward_cover.psp").read_text())
    return compile_and_flatten(spec, scenario)


def test_the_ward_really_is_impossible_before_anything_is_diagnosed():
    """Worked by hand: two nights needing two nurses is four nurse-nights, and
    with cleo on leave and a one-night limit, ada and bram supply two."""
    result, _ = run_solver(ward().flat, options=OPTIONS)
    assert result.status == SolveStatus.INFEASIBLE


def test_an_infeasible_model_is_diagnosed_rather_than_labelled():
    diagnosis = diagnose(ward(), OPTIONS)
    assert diagnosis.diagnosed
    assert diagnosis.rules_that_must_give == 1
    (conflict,) = diagnosis.conflicts
    assert conflict.name == "cover_the_night"
    assert conflict.index == ["tue"]
    assert conflict.direction == "under"
    assert conflict.shortfall == pytest.approx(2.0)
    assert conflict.rhs == pytest.approx(2.0)
    assert conflict.asks_for == pytest.approx(0.0)


def test_the_fewest_rules_are_named_not_the_first_ones_found():
    """There is more than one way to repair this ward. Letting both nurses work
    a second night also does it, and names two rules instead of one."""
    diagnosis = diagnose(ward(), OPTIONS)
    assert diagnosis.rules_that_must_give == 1
    assert {c.name for c in diagnosis.conflicts} == {"cover_the_night"}


def test_a_conflict_carries_the_words_and_the_numbers_behind_it():
    """The point of diagnosing inside the platform rather than reading a solver
    log: the row already knows what it means and where its numbers came from."""
    (conflict,) = diagnose(ward(), OPTIONS).conflicts
    assert conflict.statement == "Each night is staffed to the level it needs"
    # The set declares a label map, so the conflict is named the way the ward
    # would say it rather than the way the model indexes it.
    assert conflict.label == "cover_the_night[Tuesday night]"
    assert "needed" in {p.parameter for p in conflict.parameters}
    needed = next(p for p in conflict.parameters if p.parameter == "needed")
    assert needed.description == "Nurses this night needs"
    assert needed.sources == ["dsl"]


def test_the_resolutions_lead_with_an_exact_amount():
    diagnosis = diagnose(ward(), OPTIONS)
    first = diagnosis.resolutions[0]
    assert first.kind == "relax_rule"
    assert first.amount == pytest.approx(2.0)
    # And the parameters behind it are offered without a number, because naming
    # one would mean claiming to know which way it moves.
    assert [r.kind for r in diagnosis.resolutions[1:]] == ["change_parameter"]
    assert all(r.amount is None for r in diagnosis.resolutions[1:])


def test_the_shortfall_shrinks_as_the_shortage_is_relieved():
    """A distance from feasibility is only useful if it moves. One agency night
    takes the ward from two short to one; a full week takes it to a plan."""
    assert diagnose(ward(), OPTIONS).total_shortfall == pytest.approx(2.0)

    partial = ward("agency_for_one_night")
    assert run_solver(partial.flat, options=OPTIONS)[0].status == SolveStatus.INFEASIBLE
    assert diagnose(partial, OPTIONS).total_shortfall == pytest.approx(1.0)

    enough = ward("agency_for_the_week")
    assert run_solver(enough.flat, options=OPTIONS)[0].status == SolveStatus.OPTIMAL


def test_diagnosing_leaves_the_real_model_exactly_as_it_was():
    """The elastic copy is a question asked about the model, not a change to it.
    If diagnosis leaked, the next solve would quietly return a plan that breaks
    the rule it was asked about."""
    compiled = ward()
    before = compiled.flat.model_dump_json()
    diagnose(compiled, OPTIONS)
    assert compiled.flat.model_dump_json() == before
    assert [v for v in compiled.flat.variables if v.role == "violation"] == []
    assert run_solver(compiled.flat, options=OPTIONS)[0].status == SolveStatus.INFEASIBLE


def test_a_rule_that_already_bends_is_not_reported_as_a_conflict():
    """A soft constraint is not in the way — it is the part of the model that
    was designed to give. Only rules that were supposed to hold are conflicts.
    """
    source = """
problem mixed "Mixed"
  "One rule that may bend and two that may not."
set Days = mon, tue of day
param needed[Days] "Cover needed" = { mon: 2, tue: 2 }
var staff[Days] integer in [0, 1] means "People on this day"

constraint prefer_quiet_days "Keep the days quiet"
  category policy
  soft penalty 3
  forall d in Days:
    staff[d] <= 0

constraint cover_each_day "Each day is covered"
  category regulatory
  forall d in Days:
    staff[d] >= needed[d]

minimize used "People used" unit "people":
  sum(staff[d] for d in Days)
"""
    compiled = compile_and_flatten(parse_problem(source))
    assert run_solver(compiled.flat, options=OPTIONS)[0].status == SolveStatus.INFEASIBLE
    diagnosis = diagnose(compiled, OPTIONS)
    assert {c.name for c in diagnosis.conflicts} == {"cover_each_day"}
    assert "prefer_quiet_days" not in {c.name for c in diagnosis.conflicts}


def test_the_narrative_says_the_numbers_it_is_derived_from():
    lines = " ".join(diagnose(ward(), OPTIONS).narrative)
    assert "1 would have to give" in lines
    assert "under by 2" in lines
    assert "Each night is staffed to the level it needs" in lines


def test_a_solve_through_the_api_carries_its_diagnosis(client):
    source = (EXAMPLES / "ward_cover.psp").read_text()
    assert client.post("/api/dsl/problems", json={"source": source}).status_code == 201

    solved = client.post("/api/problems/ward_cover/solve", json={}).json()
    assert solved["solution"]["status"] == "infeasible"
    diagnosis = solved["diagnosis"]
    assert diagnosis is not None, "an infeasible solve came back with nothing to act on"
    assert diagnosis["diagnosed"] is True
    assert diagnosis["rules_that_must_give"] == 1
    assert diagnosis["conflicts"][0]["name"] == "cover_the_night"
    assert diagnosis["conflicts"][0]["shortfall"] == pytest.approx(2.0)

    # And a solve that works is not made to pay for the machinery.
    fixed = client.post(
        "/api/problems/ward_cover/solve", json={"scenario": "agency_for_the_week"}
    ).json()
    assert fixed["solution"]["status"] == "optimal"
    assert fixed["diagnosis"] is None
