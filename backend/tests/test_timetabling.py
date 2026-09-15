"""Lecture timetabling.

The headline test verifies the produced timetable against the rules *directly
from the input data*, without reusing the model that produced it. A model that
is checked only by its own constraints proves nothing about whether those
constraints say what the faculty meant.
"""

from __future__ import annotations

from collections import defaultdict

import pytest

from psp.compiler import compile_and_flatten
from psp.execution.runner import run_solver
from psp.problem.templates.registry import get
from psp.provenance import build_solution
from psp.solvers.base import SolveOptions, SolveStatus

OPTIONS = SolveOptions(time_limit_seconds=120.0)


def solve(data):
    spec = get("lecture_timetabling").build(data)
    compiled = compile_and_flatten(spec)
    result, trace = run_solver(compiled.flat, options=OPTIONS)
    return spec, compiled, result, build_solution(compiled, result), trace


def placements(solution, data):
    """(offering, room, start slot) for every scheduled session."""
    return [(d.index[0], d.index[1], int(d.index[2])) for d in solution.decisions]


def occupancy(rows, data):
    """Expand each session over the slots it actually runs for."""
    out = []
    for offering, room, start in rows:
        for k in range(int(data["offerings"][offering]["duration"])):
            out.append((offering, room, start + k))
    return out


def group_closure(data):
    """Every group a session occupies: the attendees, their cohorts, their subgroups."""
    groups = data["groups"]

    def ancestors(g):
        chain, current = [], groups[g].get("parent")
        while current:
            chain.append(current)
            current = groups[current].get("parent")
        return chain

    children = defaultdict(set)
    for g in groups:
        for a in ancestors(g):
            children[a].add(g)
    leaves = [g for g in groups if not children[g]]

    def busy(offering):
        found = set()
        for g in data["offerings"][offering]["groups"]:
            found |= {g, *ancestors(g), *children[g]}
        return found

    return busy, leaves


def test_example_timetable_satisfies_every_stated_rule():
    data = get("lecture_timetabling").example()
    _, _, result, solution, _ = solve(data)
    assert result.status == SolveStatus.OPTIMAL, result.message

    days, per_day = data["days"], data["slots_per_day"]
    rooms, groups, offerings = data["rooms"], data["groups"], data["offerings"]
    courses = data["courses"]
    rows = placements(solution, data)
    running = occupancy(rows, data)
    busy, leaves = group_closure(data)

    def slot(day, index):
        return days.index(day) * per_day + int(index)

    # Every offering gets exactly the sessions it asked for.
    counted = defaultdict(int)
    for offering, _, _ in rows:
        counted[offering] += 1
    for key, offering in offerings.items():
        assert counted[key] == offering["sessions_per_week"], key

    # No session runs past the end of its day.
    for offering, _, start in rows:
        assert start % per_day + offerings[offering]["duration"] <= per_day, offering

    # Nothing is double-booked: room, lecturer, or any group a student is in.
    for kind in ("room", "lecturer", "group"):
        occupied = defaultdict(list)
        for offering, room, at in running:
            if kind == "room":
                occupied[(room, at)].append(offering)
            elif kind == "lecturer":
                occupied[(offerings[offering]["lecturer"], at)].append(offering)
            else:
                for g in busy(offering):
                    if g in leaves:
                        occupied[(g, at)].append(offering)
        for where, found in occupied.items():
            assert len(found) == 1, f"{kind} clash at {where}: {found}"

    # Rooms are big enough and of the right type.
    for offering, room, _ in rows:
        enrolled = sum(groups[g]["size"] for g in offerings[offering]["groups"])
        assert rooms[room]["capacity"] >= enrolled, offering
        assert rooms[room]["type"] == courses[offerings[offering]["course"]]["room_type"], offering

    # Nobody is scheduled when they said they were unavailable.
    blocked_lecturers = {(r["lecturer"], slot(r["day"], r["slot"]))
                         for r in data["lecturer_unavailable"]}
    blocked_rooms = {(r["room"], slot(r["day"], r["slot"])) for r in data["room_unavailable"]}
    blocked_groups = {(r["group"], slot(r["day"], r["slot"])) for r in data["group_unavailable"]}
    for offering, room, at in running:
        assert (offerings[offering]["lecturer"], at) not in blocked_lecturers
        assert (room, at) not in blocked_rooms
        for g in busy(offering):
            assert (g, at) not in blocked_groups

    # Daily load and consecutive runs, judged per leaf group.
    max_daily = data["max_daily_per_group"]
    max_consecutive = data["max_consecutive_per_group"]
    for g in leaves:
        per_day_count = defaultdict(int)
        for offering, _, start in rows:
            if g in busy(offering):
                per_day_count[start // per_day] += 1
        assert all(c <= max_daily for c in per_day_count.values()), g

        occupied_slots = {at for offering, _, at in running if g in busy(offering)}
        for day in range(len(days)):
            run = 0
            for index in range(per_day):
                run = run + 1 if (day * per_day + index) in occupied_slots else 0
                assert run <= max_consecutive, f"{g} on day {day}"


def test_preferences_are_granted_when_they_can_be():
    """The example is built so every preference is satisfiable; a non-zero cost
    would mean the objective is not actually steering placement."""
    data = get("lecture_timetabling").example()
    _, _, result, solution, _ = solve(data)
    assert result.status == SolveStatus.OPTIMAL
    assert solution.objectives[0].value == pytest.approx(0.0)


def test_a_cohort_in_a_lab_it_cannot_fit_is_infeasible():
    """The capacity rule must bite: this is the real-world error of sending a
    whole cohort to a lab sized for a subgroup."""
    data = get("lecture_timetabling").example()
    data["offerings"]["dblab_a1"]["groups"] = ["cs_y2_a"]  # 42 students, labs seat 30
    _, _, result, _, _ = solve(data)
    assert result.status == SolveStatus.INFEASIBLE


def test_a_subgroup_lab_cannot_clash_with_its_cohorts_lecture():
    """Leaf-level conflict checking is the point of the group hierarchy.

    Squeezed to a single day with one slot, the cohort lecture and the
    subgroup lab cannot both happen — a model that only compared directly
    attending groups would happily schedule both.
    """
    data = get("lecture_timetabling").example()
    data["days"] = ["Mon"]
    data["slots_per_day"] = 1
    data["slot_labels"] = ["09:00"]
    data["lecturer_unavailable"] = []
    data["room_unavailable"] = []
    data["group_unavailable"] = []
    data["avoid_slots"] = []
    data["preferred_room"] = []
    data["offerings"] = {
        "lecture": {"course": "db_systems", "lecturer": "haddad",
                    "groups": ["cs_y2_a"], "sessions_per_week": 1, "duration": 1},
        "lab": {"course": "db_lab", "lecturer": "nasser",
                "groups": ["cs_y2_a1"], "sessions_per_week": 1, "duration": 1},
    }
    _, _, result, _, _ = solve(data)
    assert result.status == SolveStatus.INFEASIBLE

    # Give them two slots and both fit — so the clash above was the binding
    # reason, not an accident of the reduced instance.
    data["slots_per_day"] = 2
    data["slot_labels"] = ["09:00", "11:00"]
    _, _, result, _, _ = solve(data)
    assert result.status == SolveStatus.OPTIMAL


def test_two_subgroups_of_one_cohort_may_sit_labs_at_the_same_time():
    """The mirror of the test above: sibling subgroups are different students."""
    data = get("lecture_timetabling").example()
    data["days"] = ["Mon"]
    data["slots_per_day"] = 1
    data["slot_labels"] = ["09:00"]
    data["lecturer_unavailable"] = []
    data["room_unavailable"] = []
    data["group_unavailable"] = []
    data["avoid_slots"] = []
    data["preferred_room"] = []
    data["offerings"] = {
        "lab_a1": {"course": "db_lab", "lecturer": "haddad",
                   "groups": ["cs_y2_a1"], "sessions_per_week": 1, "duration": 1},
        "lab_a2": {"course": "db_lab", "lecturer": "nasser",
                   "groups": ["cs_y2_a2"], "sessions_per_week": 1, "duration": 1},
    }
    _, _, result, solution, _ = solve(data)
    assert result.status == SolveStatus.OPTIMAL
    # One slot, two labs, two different rooms.
    assert len(solution.decisions) == 2
    assert len({d.index[1] for d in solution.decisions}) == 2


def test_impossible_inputs_are_refused_before_solving():
    template = get("lecture_timetabling")

    too_long = template.example()
    too_long["offerings"]["netlab_y3"]["duration"] = 99
    with pytest.raises(ValueError, match="can never be placed"):
        template.build(too_long)

    # Plenty of seats, but not one laboratory — the failure a total room-slot
    # count would miss.
    no_lab = template.example()
    no_lab["rooms"] = {r: v for r, v in no_lab["rooms"].items() if v["type"] != "computer_lab"}
    no_lab["room_unavailable"] = []
    with pytest.raises(ValueError, match="no room of that type"):
        template.build(no_lab)

    # Enough rooms of the right type, but nowhere near enough slots. Two slots
    # so the two-slot labs clear the duration check and this one is what fires.
    too_few_slots = template.example()
    too_few_slots["days"] = ["Mon"]
    too_few_slots["slots_per_day"] = 2
    too_few_slots["slot_labels"] = ["09:00", "11:00"]
    too_few_slots["lecturer_unavailable"] = []
    too_few_slots["room_unavailable"] = []
    too_few_slots["group_unavailable"] = []
    too_few_slots["avoid_slots"] = []
    with pytest.raises(ValueError, match="no timetable can exist"):
        template.build(too_few_slots)

    unknown = template.example()
    unknown["offerings"]["os_y3"]["lecturer"] = "nobody"
    with pytest.raises(ValueError, match="unknown lecturer"):
        template.build(unknown)

    bad_parent = template.example()
    bad_parent["groups"]["cs_y2_a1"]["parent"] = "does_not_exist"
    with pytest.raises(ValueError, match="unknown parent group"):
        template.build(bad_parent)

    mismatched = template.example()
    mismatched["slot_labels"] = ["09:00"]
    with pytest.raises(ValueError, match="slot times were given"):
        template.build(mismatched)


def test_scenarios_are_recorded_and_change_the_outcome():
    template = get("lecture_timetabling")
    spec = template.build(template.example())

    baseline = compile_and_flatten(spec)
    result, _ = run_solver(baseline.flat, options=OPTIONS)
    assert result.status == SolveStatus.OPTIMAL

    # Closing both laboratories leaves the lab courses with nowhere to go.
    closed = compile_and_flatten(spec, "lab_closed")
    assert closed.record.applied_overrides
    result, _ = run_solver(closed.flat, options=OPTIONS)
    assert result.status == SolveStatus.INFEASIBLE


def test_model_is_fully_discrete_so_cpsat_takes_it():
    template = get("lecture_timetabling")
    compiled = compile_and_flatten(template.build(template.example()))
    stats = compiled.flat.stats()
    assert stats["is_integer"]
    assert set(stats["variable_kinds"]) == {"binary"}
    _, trace = run_solver(compiled.flat, options=OPTIONS)
    assert trace["solver"] == "cpsat"


def test_slots_read_as_times_everywhere_they_are_shown():
    """A numeric slot has to stay numeric for the compiler and read as a time
    for the reader. The label map is what reconciles those, so it has to reach
    decisions, binding constraints and the narrative alike."""
    from psp.provenance import explain_decision

    data = get("lecture_timetabling").example()
    spec, compiled, result, solution, _ = solve(data)
    assert result.status == SolveStatus.OPTIMAL

    # The set still carries plain integers — labelling must not have leaked
    # into the elements the compiler does arithmetic on.
    slots = compiled.ir.set("Slots")
    assert slots.kind == "int"
    assert all(e.isdigit() for e in slots.elements)
    assert slots.labels["23"] == "Thu 13:45"

    for decision in solution.decisions:
        assert decision.label, decision.key
        # The raw key stays the identifier; the label is display only.
        assert decision.key in solution.values
        slot = decision.index[2]
        assert slots.labels[slot] in decision.label
        assert decision.variable in decision.label

    slot_indexed = [
        c for c in solution.binding_constraints
        if c.name in ("lecturer_no_overlap", "room_no_overlap", "group_no_overlap")
    ]
    assert slot_indexed, "no slot-indexed constraint was binding"
    for outcome in slot_indexed:
        assert outcome.label and ":" in outcome.label, outcome.key

    explanation = explain_decision(compiled, solution, solution.decisions[0].key)
    assert explanation.label
    assert ":" in explanation.narrative[0], explanation.narrative[0]
    # The explain API is still addressed by the raw key, so a caller never has
    # to parse a display string back into an index.
    assert explanation.decision == solution.decisions[0].key


def test_labels_are_optional_and_absent_where_no_set_declares_them():
    """Models without labels must be unaffected, and must not pay for the
    feature by carrying a copy of every key."""
    template = get("assignment")
    spec = template.build(template.example())
    compiled = compile_and_flatten(spec)
    result, _ = run_solver(compiled.flat, options=OPTIONS)
    solution = build_solution(compiled, result)

    assert all(not s.labels for s in compiled.ir.sets)
    assert all(d.label is None for d in solution.decisions)
    assert all(c.label is None for c in solution.binding_constraints)


def test_a_partial_label_map_falls_back_per_element():
    """Labelling some elements and not others must not produce a broken key."""
    from psp.provenance.labels import LabelResolver

    template = get("lecture_timetabling")
    spec = template.build(template.example())
    spec.sets = [
        s.model_copy(update={"labels": {"0": "Sun 08:30"}}) if s.name == "Slots" else s
        for s in spec.sets
    ]
    compiled = compile_and_flatten(spec)
    labels = LabelResolver(compiled.ir)

    assert labels.variable("place", ["db_y2", "hall_a", "0"]) == "place[db_y2, hall_a, Sun 08:30]"
    # An unlabelled element renders as itself rather than blank or missing.
    assert labels.variable("place", ["db_y2", "hall_a", "7"]) == "place[db_y2, hall_a, 7]"
