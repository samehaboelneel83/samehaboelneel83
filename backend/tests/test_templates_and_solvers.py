"""Every template must compile and solve, and the answer must not depend on
which engine happened to be chosen."""

from __future__ import annotations

import pytest

from psp.compiler import compile_and_flatten
from psp.execution.runner import run_solver
from psp.problem.templates.registry import available, get
from psp.provenance import build_solution, explain_decision
from psp.solvers.base import SolveOptions, SolveStatus
from psp.solvers.registry import UnsupportedModelError, select

OPTIONS = SolveOptions(time_limit_seconds=30.0)


@pytest.mark.parametrize("key", available())
def test_template_example_compiles_and_solves(key):
    template = get(key)
    compiled = compile_and_flatten(template.build(template.example()))
    result, trace = run_solver(compiled.flat, options=OPTIONS)
    assert result.status == SolveStatus.OPTIMAL, f"{key}: {result.message}"

    solution = build_solution(compiled, result)
    assert solution.objectives, f"{key} reported no objective value"
    assert solution.decisions, f"{key} recommended nothing"
    # Every engine that was rejected must say why; a silent rejection is
    # untraceable in an audit.
    for entry in trace["considered"]:
        assert entry["eligible"] or entry["reason"]


def test_objectives_are_reported_in_their_own_direction():
    template = get("resource_allocation")
    compiled = compile_and_flatten(template.build(template.example()))
    result, _ = run_solver(compiled.flat, options=OPTIONS)
    solution = build_solution(compiled, result)

    value = solution.objectives[0]
    assert value.sense == "maximize"
    # The internal minimisation carries the negated value; reporting that number
    # to a user would invert the meaning of the answer.
    assert value.value > 0
    assert result.objective_value == pytest.approx(-value.value)


def test_same_problem_gives_same_optimum_through_different_engines():
    """The point of the IR: swapping the engine must not change the answer."""
    template = get("transportation")
    data = template.example()
    data.pop("lane_capacity")  # uncapacitated, so the flow structure applies

    compiled = compile_and_flatten(template.build(data))
    assert compiled.flat.structure["kind"] == "min_cost_flow"

    by_highs, _ = run_solver(compiled.flat, solver="highs", options=OPTIONS)
    by_network, _ = run_solver(compiled.flat, solver="networkx", options=OPTIONS)

    assert by_highs.status == by_network.status == SolveStatus.OPTIMAL
    assert by_highs.objective_value == pytest.approx(by_network.objective_value, rel=1e-6)


def test_discrete_model_agrees_between_cpsat_and_highs():
    template = get("assignment")
    compiled = compile_and_flatten(template.build(template.example()))
    by_cpsat, _ = run_solver(compiled.flat, solver="cpsat", options=OPTIONS)
    by_highs, _ = run_solver(compiled.flat, solver="highs", options=OPTIONS)
    assert by_cpsat.objective_value == pytest.approx(by_highs.objective_value, rel=1e-6)


def test_solver_selection_prefers_the_specialised_engine():
    template = get("transportation")
    data = template.example()
    data.pop("lane_capacity")
    compiled = compile_and_flatten(template.build(data))
    chosen, considered = select(compiled.flat)
    assert chosen == "networkx"
    assert {c["solver"] for c in considered} == {"networkx", "cpsat", "highs"}


def test_explicitly_requested_solver_is_never_silently_substituted():
    """Asking for an engine that cannot take the model must fail, not fall back.

    A run record that names a solver the user did not choose is worse than no
    answer, because nothing downstream would reveal the substitution.
    """
    template = get("transportation")
    compiled = compile_and_flatten(template.build(template.example()))
    with pytest.raises(UnsupportedModelError, match="fully discrete"):
        run_solver(compiled.flat, solver="cpsat", options=OPTIONS)


def test_unknown_solver_is_rejected():
    template = get("assignment")
    compiled = compile_and_flatten(template.build(template.example()))
    with pytest.raises(KeyError, match="unknown solver"):
        run_solver(compiled.flat, solver="gurobi", options=OPTIONS)


def test_scenarios_change_the_answer_and_record_what_changed():
    template = get("resource_allocation")
    spec = template.build(template.example())

    baseline = compile_and_flatten(spec)
    austerity = compile_and_flatten(spec, "austerity")

    assert baseline.ir.fingerprint() != austerity.ir.fingerprint()
    assert austerity.record.applied_overrides
    for override in austerity.record.applied_overrides:
        assert override["value"] == pytest.approx(override["baseline"] * 0.8)

    base_result, _ = run_solver(baseline.flat, options=OPTIONS)
    cut_result, _ = run_solver(austerity.flat, options=OPTIONS)
    base_value = build_solution(baseline, base_result).objectives[0].value
    cut_value = build_solution(austerity, cut_result).objectives[0].value
    assert cut_value < base_value, "cutting every resource must not increase value"


def test_infeasible_scenario_is_reported_not_hidden():
    template = get("transportation")
    spec = template.build(template.example())
    compiled = compile_and_flatten(spec, "source_lost")
    result, _ = run_solver(compiled.flat, solver="highs", options=OPTIONS)
    assert result.status == SolveStatus.INFEASIBLE


def test_explanation_cites_a_binding_constraint_and_its_sources():
    template = get("resource_allocation")
    compiled = compile_and_flatten(template.build(template.example()))
    result, _ = run_solver(compiled.flat, solver="highs", options=OPTIONS)
    solution = build_solution(compiled, result)

    decision = solution.decisions[0]
    explanation = explain_decision(compiled, solution, decision.key)
    assert explanation.value == pytest.approx(decision.value)
    assert explanation.narrative
    assert explanation.assumptions, "an explanation without its assumptions is incomplete"

    cited = explanation.limited_by + explanation.also_appears_in
    assert cited, "the decision appears in no constraint at all"
    assert any(e.statement for e in cited), "no cited constraint has a plain-language statement"


def test_shadow_price_is_signed_for_the_stated_objective():
    """A maximisation and a minimisation must not report the same sign for
    'more capacity'."""
    allocation = get("resource_allocation")
    compiled = compile_and_flatten(allocation.build(allocation.example()))
    result, _ = run_solver(compiled.flat, solver="highs", options=OPTIONS)
    solution = build_solution(compiled, result)
    capacity_rows = [
        o for o in solution.binding_constraints
        if o.name == "respect_capacity" and o.marginal_effect
    ]
    assert capacity_rows, "no resource limit was binding, so there is nothing to check"
    # More of a scarce resource can only help a maximisation.
    assert all(row.marginal_effect > 0 for row in capacity_rows)

    transport = get("transportation")
    data = transport.example()
    data.pop("lane_capacity")
    compiled = compile_and_flatten(transport.build(data))
    result, _ = run_solver(compiled.flat, solver="highs", options=OPTIONS)
    solution = build_solution(compiled, result)
    demand_rows = [
        o for o in solution.binding_constraints
        if o.name == "meet_demand" and o.marginal_effect
    ]
    assert demand_rows
    # More demand can only cost more in a minimisation.
    assert all(row.marginal_effect > 0 for row in demand_rows)


def test_template_rejects_impossible_data_before_solving():
    template = get("vehicle_routing")
    data = template.example()
    data["vehicle_capacity"] = 1  # far below total demand
    with pytest.raises(ValueError, match="exceeds the fleet's capacity"):
        template.build(data)

    scheduling = get("scheduling")
    data = scheduling.example()
    data["horizon"] = 2
    with pytest.raises(ValueError, match="shorter than the longest task"):
        scheduling.build(data)


def test_missing_duals_name_their_actual_cause():
    """Absent shadow prices have several causes, and they need different fixes.

    Reporting "integer model" for a continuous problem solved by a
    combinatorial engine sends the reader to sensitivity analysis when
    re-solving with an LP engine would have answered the question.
    """
    transport = get("transportation")
    data = transport.example()
    data.pop("lane_capacity")
    compiled = compile_and_flatten(transport.build(data))

    by_network, _ = run_solver(compiled.flat, solver="networkx", options=OPTIONS)
    network_solution = build_solution(compiled, by_network)
    assert network_solution.duals_available is False
    assert "networkx" in network_solution.duals_unavailable_reason
    assert "integer" not in network_solution.duals_unavailable_reason

    by_highs, _ = run_solver(compiled.flat, solver="highs", options=OPTIONS)
    highs_solution = build_solution(compiled, by_highs)
    assert highs_solution.duals_available is True
    assert highs_solution.duals_unavailable_reason is None

    assignment = get("assignment")
    compiled = compile_and_flatten(assignment.build(assignment.example()))
    result, _ = run_solver(compiled.flat, solver="highs", options=OPTIONS)
    integer_solution = build_solution(compiled, result)
    assert integer_solution.duals_available is False
    assert "integer variables" in integer_solution.duals_unavailable_reason


def test_sentinel_capacities_do_not_become_constraint_rows():
    """A bound of "no limit" constrains nothing and must not cost a row."""
    template = get("transportation")

    uncapped = template.example()
    uncapped.pop("lane_capacity")
    without = compile_and_flatten(template.build(uncapped))
    assert not any(c.name == "lane_limit" for c in without.flat.constraints)

    capped = template.example()
    with_caps = compile_and_flatten(template.build(capped))
    lane_rows = [c for c in with_caps.flat.constraints if c.name == "lane_limit"]
    # Exactly the lanes that were actually given a cap, not every lane.
    assert len(lane_rows) == len(capped["lane_capacity"])


def test_a_solve_returns_the_same_plan_in_a_different_process():
    """Solves get several workers by default, and that has to be safe.

    Solved with four workers deliberately, which is not the default: one worker
    cannot race, so the default could not catch the thing this guards. Several
    workers left to themselves do race — whichever reaches an optimum first
    decides which of several equally good plans comes back — and interleaving
    them is what makes the plan, and not merely its objective value, a property
    of the problem.

    This is a sample, not a proof: it caught the racing version only once the
    machine was slowed down, having passed for some time before that. The
    guarantee lives in the solver parameters; this is what would notice if it
    were lost.
    """
    import os
    import pathlib as _pathlib
    import subprocess
    import sys

    script = (
        "import sys, json; sys.path.insert(0, '.');"
        "from psp.compiler import compile_and_flatten;"
        "from psp.dsl import parse_problem;"
        "from psp.execution.runner import run_solver;"
        "from psp.solvers.base import SolveOptions;"
        "spec = parse_problem(open('../examples/commitment_planning.psp').read());"
        "flat = compile_and_flatten(spec).flat;"
        "r, meta = run_solver(flat, "
        "options=SolveOptions(time_limit_seconds=120, threads=4));"
        "print(meta['solver'], r.status.value, r.objective_value, "
        "json.dumps(sorted(k for k, v in r.values.items() if v > 0.5)))"
    )
    root = _pathlib.Path(__file__).resolve().parents[1]
    runs = []
    for seed in ("3", "11"):
        finished = subprocess.run(
            [sys.executable, "-c", script], cwd=root, capture_output=True, text=True,
            env={**os.environ, "PYTHONHASHSEED": seed}, check=True,
        )
        runs.append(finished.stdout.strip())
    assert runs[0].split()[1] == "optimal"
    assert runs[0] == runs[1], "the same model gave two different plans"


def test_an_engine_that_cannot_represent_the_data_says_so_before_running():
    """Capability is checked before a run, not discovered during one.

    CP-SAT works in exact integer arithmetic, so a coefficient that will not
    scale to a whole number is something it cannot take. Until this was a
    capability the registry called it eligible, chose it, and returned an error
    from inside the solve — while HiGHS, also eligible, would have answered.
    """
    from psp.dsl import parse_problem

    compiled = compile_and_flatten(parse_problem('''
problem awkward "Awkward"
  "One coefficient that no common multiplier makes whole."
set A = a, b of thing
param w[A] = { a: 1000000, b: 0.0000001 }
var v[A] binary means "Take it"
constraint at_least_one "At least one is taken"
  sum(v[i] for i in A) >= 1
minimize cost "What it costs" unit "cost":
  sum(w[i] * v[i] for i in A)
'''))
    result, meta = run_solver(compiled.flat, options=OPTIONS)

    cpsat = next(c for c in meta["considered"] if c["solver"] == "cpsat")
    assert cpsat["eligible"] is False
    assert "exact integer arithmetic" in cpsat["reason"]
    assert "1e-07" in cpsat["reason"], "the refusal does not name the coefficient"

    # And the model is still solved, by the engine that can take it.
    assert meta["solver"] == "highs"
    assert result.status == SolveStatus.OPTIMAL
