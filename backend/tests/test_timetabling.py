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

    # Nothing is taught during a fixed event, for anyone it stops.
    for event in data.get("fixed_events") or []:
        event_days = event.get("days") or [event["day"]]
        periods = event.get("slots")
        periods = range(per_day) if periods is None else periods
        blocked = {slot(day, index) for day in event_days for index in periods}
        wide = not any(event.get(k) for k in ("groups", "rooms", "lecturers"))
        for offering, room, at in running:
            if at not in blocked:
                continue
            assert wide is False, f"{offering} runs during faculty-wide {event['key']}"
            assert room not in (event.get("rooms") or []), f"{room} used during {event['key']}"
            assert offerings[offering]["lecturer"] not in (event.get("lecturers") or [])
            assert not busy(offering) & set(event.get("groups") or []), (
                f"{offering} runs during {event['key']}"
            )

    # Nobody is asked to cross campus faster than the walk allows. Checked for
    # student groups and lecturers alike, straight from the travel table.
    travel = {}
    for row in data.get("travel_slots") or []:
        travel[(row["from"], row["to"])] = row["slots"]
        travel[(row["to"], row["from"])] = row["slots"]

    def building(room):
        return rooms[room]["building"]

    for first in running:
        for second in running:
            gap = second[2] - first[2]
            if gap <= 0 or first[2] // per_day != second[2] // per_day:
                continue
            needed = travel.get((building(first[1]), building(second[1])), 0)
            if gap > needed:
                continue
            shared_group = busy(first[0]) & busy(second[0]) & set(leaves)
            same_lecturer = (
                offerings[first[0]]["lecturer"] == offerings[second[0]]["lecturer"]
            )
            assert not shared_group and not same_lecturer, (
                f"{first[0]} in {building(first[1])} at {first[2]} then {second[0]} in "
                f"{building(second[1])} at {second[2]}: {gap} period(s) apart, needs {needed}"
            )

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
    data["fixed_events"] = []
    data["avoid_slots"] = []
    data["preferred_room"] = []
    data["travel_slots"] = []  # isolate the hierarchy rule from the travel rule
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
    data["fixed_events"] = []
    data["avoid_slots"] = []
    data["preferred_room"] = []
    data["travel_slots"] = []
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


def test_travel_time_forbids_a_walk_that_does_not_fit():
    """Two sessions the same people must attend, back to back in buildings a
    period apart, cannot both happen. Without the travel rule the solver would
    happily schedule them."""
    template = get("lecture_timetabling")
    data = template.example()
    data["days"] = ["Mon"]
    data["slots_per_day"] = 2
    data["slot_labels"] = ["09:00", "11:00"]
    data["lecturer_unavailable"] = []
    data["room_unavailable"] = []
    data["group_unavailable"] = []
    data["fixed_events"] = []
    data["avoid_slots"] = []
    data["preferred_room"] = []
    data["max_consecutive_per_group"] = 2
    # One hall in the main building, one lab in the annex, nothing else.
    data["rooms"] = {
        "hall_a": {"capacity": 120, "type": "lecture_hall", "building": "main"},
        "lab_1": {"capacity": 30, "type": "computer_lab", "building": "annex"},
    }
    # One cohort with a lecture and a lab: two sessions, two slots, no choice
    # but to place them back to back.
    data["offerings"] = {
        "lecture": {"course": "db_systems", "lecturer": "haddad",
                    "groups": ["cs_y2_a1"], "sessions_per_week": 1, "duration": 1},
        "lab": {"course": "db_lab", "lecturer": "haddad",
                "groups": ["cs_y2_a1"], "sessions_per_week": 1, "duration": 1},
    }

    data["travel_slots"] = []
    _, _, result, _, _ = solve(data)
    assert result.status == SolveStatus.OPTIMAL, "the instance must be solvable without travel"

    data["travel_slots"] = [{"from": "main", "to": "annex", "slots": 1}]
    _, _, result, _, _ = solve(data)
    assert result.status == SolveStatus.INFEASIBLE

    # Three slots leave a free period between them, so the walk fits.
    data["slots_per_day"] = 3
    data["slot_labels"] = ["09:00", "11:00", "13:00"]
    _, _, result, solution, _ = solve(data)
    assert result.status == SolveStatus.OPTIMAL
    starts = sorted(int(d.index[2]) for d in solution.decisions)
    assert starts[1] - starts[0] >= 2, starts


def test_travel_applies_to_lecturers_not_only_students():
    """A lecturer walks at the same speed as a student."""
    template = get("lecture_timetabling")
    data = template.example()
    data["days"] = ["Mon"]
    data["slots_per_day"] = 2
    data["slot_labels"] = ["09:00", "11:00"]
    data["lecturer_unavailable"] = []
    data["room_unavailable"] = []
    data["group_unavailable"] = []
    data["fixed_events"] = []
    data["avoid_slots"] = []
    data["preferred_room"] = []
    data["rooms"] = {
        "hall_a": {"capacity": 120, "type": "lecture_hall", "building": "main"},
        "lab_1": {"capacity": 30, "type": "computer_lab", "building": "annex"},
    }
    data["travel_slots"] = [{"from": "main", "to": "annex", "slots": 1}]
    # Different cohorts, so no student is asked to cross — only the lecturer is.
    data["offerings"] = {
        "lecture": {"course": "db_systems", "lecturer": "haddad",
                    "groups": ["cs_y2_a1"], "sessions_per_week": 1, "duration": 1},
        "lab": {"course": "db_lab", "lecturer": "haddad",
                "groups": ["cs_y2_b1"], "sessions_per_week": 1, "duration": 1},
    }
    _, _, result, _, _ = solve(data)
    assert result.status == SolveStatus.INFEASIBLE

    # Hand the lab to someone else and both fit in the same two periods.
    data["offerings"]["lab"]["lecturer"] = "nasser"
    _, _, result, _, _ = solve(data)
    assert result.status == SolveStatus.OPTIMAL


def test_travel_costs_nothing_when_the_campus_is_walkable():
    """With no travel declared the rule is left out entirely rather than
    generated and trivially satisfied."""
    template = get("lecture_timetabling")
    data = template.example()
    data["travel_slots"] = []
    compiled = compile_and_flatten(template.build(data))
    names = {c.name for c in compiled.flat.constraints}
    assert "group_travel_time" not in names
    assert "lecturer_travel_time" not in names
    assert not any(s.name == "Gaps" for s in compiled.ir.sets)

    with_travel = compile_and_flatten(template.build(template.example()))
    assert "group_travel_time" in {c.name for c in with_travel.flat.constraints}


def test_travel_longer_than_a_day_means_one_building_per_day():
    """A walk longer than the teaching day is a strong statement, not an error:
    the same people cannot use both buildings that day."""
    template = get("lecture_timetabling")
    data = template.example()
    data["travel_slots"] = [{"from": "main", "to": "annex", "slots": 99}]
    compiled = compile_and_flatten(template.build(data))

    # Capped at the widest gap that can still fall inside one day.
    gaps = next(s for s in compiled.ir.sets if s.name == "Gaps")
    assert gaps.elements == ["1", "2", "3", "4"]

    result, _ = run_solver(compiled.flat, options=OPTIONS)
    if result.status == SolveStatus.OPTIMAL:
        solution = build_solution(compiled, result)
        rooms = data["rooms"]
        busy, leaves = group_closure(data)
        per_day = defaultdict(lambda: defaultdict(set))
        for d in solution.decisions:
            offering, room, at = d.index[0], d.index[1], int(d.index[2])
            for g in busy(offering):
                if g in leaves:
                    per_day[(g, at // data["slots_per_day"])][g].add(rooms[room]["building"])
        for (g, day), found in per_day.items():
            assert len(found[g]) == 1, f"{g} uses {found[g]} on day {day}"

    negative = template.example()
    negative["travel_slots"] = [{"from": "main", "to": "annex", "slots": -1}]
    with pytest.raises(ValueError, match="cannot be negative"):
        template.build(negative)

    unknown = template.example()
    unknown["travel_slots"] = [{"from": "main", "to": "nowhere", "slots": 1}]
    with pytest.raises(ValueError, match="unknown building"):
        template.build(unknown)


def test_a_multi_period_session_may_not_run_through_a_closure():
    """Regression: availability was checked only at the slot a session starts
    in, so a two-period lab could begin while the room was open and run
    straight through the hour it shut."""
    template = get("lecture_timetabling")

    def instance(closed_slot):
        data = template.example()
        data["days"] = ["Mon"]
        data["slots_per_day"] = 3
        data["slot_labels"] = ["09:00", "11:00", "13:00"]
        data["lecturer_unavailable"] = []
        data["group_unavailable"] = []
        data["fixed_events"] = []
        data["avoid_slots"] = []
        data["preferred_room"] = []
        data["travel_slots"] = []
        data["rooms"] = {"lab_1": {"capacity": 30, "type": "computer_lab",
                                   "building": "annex"}}
        data["offerings"] = {
            "lab": {"course": "db_lab", "lecturer": "haddad",
                    "groups": ["cs_y2_a1"], "sessions_per_week": 1, "duration": 2},
        }
        data["room_unavailable"] = [{"room": "lab_1", "day": "Mon", "slot": closed_slot}]
        return data

    # Shut for the last period: the session must take the first two, since
    # starting second would run into the closure.
    _, _, result, solution, _ = solve(instance(2))
    assert result.status == SolveStatus.OPTIMAL
    assert [int(d.index[2]) for d in solution.decisions] == [0]

    # Shut for the middle period: no two consecutive open periods remain.
    _, _, result, _, _ = solve(instance(1))
    assert result.status == SolveStatus.INFEASIBLE


def test_a_lecturer_may_not_be_scheduled_through_their_unavailability():
    """The same defect, for the lecturer and group tables rather than the room."""
    template = get("lecture_timetabling")
    data = template.example()
    data["days"] = ["Mon"]
    data["slots_per_day"] = 3
    data["slot_labels"] = ["09:00", "11:00", "13:00"]
    data["room_unavailable"] = []
    data["group_unavailable"] = []
    data["fixed_events"] = []
    data["avoid_slots"] = []
    data["preferred_room"] = []
    data["travel_slots"] = []
    data["rooms"] = {"lab_1": {"capacity": 30, "type": "computer_lab", "building": "annex"}}
    data["offerings"] = {
        "lab": {"course": "db_lab", "lecturer": "haddad",
                "groups": ["cs_y2_a1"], "sessions_per_week": 1, "duration": 2},
    }
    data["lecturer_unavailable"] = [{"lecturer": "haddad", "day": "Mon", "slot": 1}]
    _, _, result, _, _ = solve(data)
    assert result.status == SolveStatus.INFEASIBLE

    data["lecturer_unavailable"] = []
    data["group_unavailable"] = [{"group": "cs_y2_a", "day": "Mon", "slot": 1}]
    _, _, result, _, _ = solve(data)
    assert result.status == SolveStatus.INFEASIBLE


def test_a_faculty_wide_event_stops_everything():
    """An event naming no group, room or lecturer is a public holiday. Reading
    that as "stops nobody" would be the dangerous default."""
    template = get("lecture_timetabling")
    data = template.example()
    data["fixed_events"] = [
        {"key": "national_day", "name": "National day", "kind": "holiday", "day": "Wed"},
    ]
    _, compiled, result, solution, _ = solve(data)
    assert result.status == SolveStatus.OPTIMAL

    per_day = data["slots_per_day"]
    wednesday = range(data["days"].index("Wed") * per_day,
                      (data["days"].index("Wed") + 1) * per_day)
    for offering, _, at in occupancy(placements(solution, data), data):
        assert at not in wednesday, f"{offering} scheduled on the holiday"

    # Every kind of availability was closed, not just the students'.
    for name in ("group_available", "room_available", "lecturer_available"):
        table = compiled.ir.param(name)
        assert any(
            table.get((subject, str(at))) == 0.0
            for at in wednesday
            for subject in compiled.ir.set(table.index_sets[0]).elements
        ), name


def test_a_scoped_event_stops_only_what_it_names():
    """An exam takes its cohorts, its hall and its invigilator out; everyone
    else carries on."""
    template = get("lecture_timetabling")
    data = template.example()
    data["fixed_events"] = [
        {"key": "y2_exam", "name": "Year 2 exam", "kind": "exam", "day": "Wed", "slots": [0, 1],
         "groups": ["cs_y2_a", "cs_y2_b"], "rooms": ["hall_a"], "lecturers": ["haddad"]},
    ]
    _, compiled, result, solution, _ = solve(data)
    assert result.status == SolveStatus.OPTIMAL

    per_day = data["slots_per_day"]
    exam_slots = {data["days"].index("Wed") * per_day + i for i in (0, 1)}
    busy, _ = group_closure(data)
    for offering, room, at in occupancy(placements(solution, data), data):
        if at not in exam_slots:
            continue
        assert not busy(offering) & {"cs_y2_a", "cs_y2_b", "cs_y2_a1", "cs_y2_a2",
                                     "cs_y2_b1", "cs_y2_b2"}
        assert room != "hall_a"
        assert data["offerings"][offering]["lecturer"] != "haddad"

    # Year 3 is untouched by the exam. Checked on its second period, since the
    # example already blocks year 3 for a project seminar in the first.
    second = max(exam_slots)
    assert compiled.ir.param("group_available").get(("cs_y3_a", str(second))) == 1.0
    assert compiled.ir.param("room_available").get(("hall_b", str(second))) == 1.0


def test_a_blocked_period_says_which_event_blocked_it():
    """The point of declaring an event rather than typing out unavailability:
    the block keeps its reason, so an explanation can name it."""
    template = get("lecture_timetabling")
    spec = template.build(template.example())

    blocked = [
        v for v in spec.parameter("group_available").values
        if v.value == 0.0 and v.origin and v.origin.source.startswith("fixed_event:")
    ]
    assert blocked, "no group period was attributed to an event"
    sources = {v.origin.source for v in blocked}
    assert "fixed_event:spring_holiday" in sources
    notes = {v.origin.note for v in blocked}
    assert any(note and note.startswith("holiday:") for note in notes)

    # A subgroup inherits both the block and the reason from its cohort.
    exam_cells = [
        v for v in spec.parameter("group_available").values
        if v.origin and v.origin.source == "fixed_event:db_midterm"
    ]
    assert {v.index[0] for v in exam_cells} >= {"cs_y2_a", "cs_y2_a1", "cs_y2_a2"}

    # Ordinary unavailability is still plain user input, not dressed up as an event.
    typed = [
        v for v in spec.parameter("lecturer_available").values
        if v.value == 0.0 and v.origin and v.origin.source == "user_input"
    ]
    assert typed, "hand-entered unavailability lost its own origin"


def test_omitting_the_periods_blocks_the_whole_day():
    template = get("lecture_timetabling")
    data = template.example()
    data["fixed_events"] = [
        {"key": "closure", "name": "Campus closed", "kind": "holiday", "day": "Thu"},
    ]
    spec = template.build(data)
    per_day = data["slots_per_day"]
    thursday = [data["days"].index("Thu") * per_day + i for i in range(per_day)]
    table = spec.parameter("room_available")
    for at in thursday:
        assert any(v.value == 0.0 and v.index == ["lab_1", str(at)] for v in table.values)


def test_events_spanning_several_days_are_accepted():
    template = get("lecture_timetabling")
    data = template.example()
    data["fixed_events"] = [
        {"key": "exam_week", "name": "Examination period", "kind": "exam",
         "days": ["Wed", "Thu"], "slots": [3, 4]},
    ]
    spec = template.build(data)
    event = next(e for e in spec.metadata["fixed_events"] if e["key"] == "exam_week")
    assert event["days"] == ["Wed", "Thu"]
    assert event["faculty_wide"] is True
    compiled = compile_and_flatten(spec)
    result, _ = run_solver(compiled.flat, options=OPTIONS)
    assert result.status == SolveStatus.OPTIMAL


def test_malformed_events_are_refused():
    template = get("lecture_timetabling")

    for event, message in (
        ({"name": "no key", "day": "Mon"}, "needs a key"),
        ({"key": "e1", "name": "no day"}, "which day"),
        ({"key": "e2", "name": "bad day", "day": "Caturday"}, "not one of the teaching days"),
        ({"key": "e3", "name": "bad slot", "day": "Mon", "slots": [99]}, "outside the"),
        ({"key": "e4", "name": "bad group", "day": "Mon", "groups": ["nobody"]},
         "unknown group"),
        ({"key": "e5", "name": "bad room", "day": "Mon", "rooms": ["nowhere"]}, "unknown room"),
        ({"key": "e6", "name": "bad staff", "day": "Mon", "lecturers": ["ghost"]},
         "unknown lecturer"),
    ):
        data = template.example()
        data["fixed_events"] = [event]
        with pytest.raises(ValueError, match=message):
            template.build(data)


def test_an_explanation_names_the_event_that_removed_the_alternatives():
    """A rule that forbids something never mentions the placement that was
    chosen, so a fixed event could never appear in `limited_by`. Without
    `ruled_out` the event provenance would be unreachable — which is the whole
    reason to declare an event rather than type out unavailability."""
    from psp.provenance import explain_decision

    template = get("lecture_timetabling")
    spec, compiled, result, solution, _ = solve(template.example())
    assert result.status == SolveStatus.OPTIMAL

    explanation = explain_decision(compiled, solution, solution.decisions[0].key)
    assert explanation.ruled_out, "nothing was recorded as closing off the alternatives"

    # No ruled-out row may mention the chosen placement: that is what makes it
    # a rule about the alternatives rather than about this decision.
    for evidence in explanation.ruled_out:
        row = next(c for c in compiled.flat.constraints if c.key == evidence.key)
        assert explanation.decision not in row.terms
        assert evidence.alternatives_removed and evidence.alternatives_removed > 0

    named = {
        source
        for evidence in explanation.ruled_out
        for parameter in evidence.parameters
        for source in parameter.sources
    }
    assert any(s.startswith("fixed_event:") for s in named), named
    assert "fixed_event:spring_holiday" in named

    joined = "\n".join(explanation.narrative)
    assert "Other options for it were removed by" in joined
    assert "fixed_event:" in joined


def test_ruled_out_only_counts_the_same_subject():
    """The count must be the options removed for *this* offering, not every
    placement the rule forbids across the faculty."""
    from psp.provenance import explain_decision

    template = get("lecture_timetabling")
    _, compiled, result, solution, _ = solve(template.example())
    decision = solution.decisions[0]
    explanation = explain_decision(compiled, solution, decision.key)
    offering = decision.index[0]

    for evidence in explanation.ruled_out:
        row = next(c for c in compiled.flat.constraints if c.key == evidence.key)
        mine = [
            key for key in row.terms
            if (v := compiled.flat.var_index.get(key)) and v.index[:1] == [offering]
        ]
        assert evidence.alternatives_removed == len(mine)
        assert len(mine) <= len(row.terms)


def test_models_without_forbidding_rules_have_nothing_ruled_out():
    from psp.provenance import explain_decision

    template = get("transportation")
    data = template.example()
    data.pop("lane_capacity")
    spec = template.build(data)
    compiled = compile_and_flatten(spec)
    result, _ = run_solver(compiled.flat, solver="highs", options=OPTIONS)
    solution = build_solution(compiled, result)
    explanation = explain_decision(compiled, solution, solution.decisions[0].key)
    assert explanation.ruled_out == []
