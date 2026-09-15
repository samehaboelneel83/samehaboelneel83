"""University lecture timetabling.

The unit being scheduled is the **course offering**, not the course. A course
("Database Systems") is a catalogue entry; an offering ("Database Systems,
2026/27 semester 1, Computer Science year 3, group A, Dr Haddad") is a concrete
commitment with a lecturer, a set of student groups, a weekly session count and
a room requirement. Only the second can be placed in a room at a time.

Formulation
-----------
``place[offering, room, slot] = 1`` when a session of that offering *starts* in
that room at that slot. Slots are numbered across the whole week, so slot
arithmetic crosses days and a day boundary is a parameter rather than a special
case. A session of length d occupies slots s .. s+d-1, so the resource in use at
slot t is the sum of starts at t, t-1, ... t-d+1 — which is why the model
carries a small ``Offsets`` set rather than scanning the whole week for every
conflict check.

Groups and subgroups
--------------------
A cohort attends lectures together but splits into subgroups for laboratory
work, so a group may declare a ``parent``. Two different relations follow from
that, and conflating them is the classic timetabling bug:

* ``enrolment`` counts only the groups *directly* attending, so a lab for 21
  students is checked against a 30-seat lab, not against the 42-strong cohort.
* ``busy`` is the transitive relation — a cohort's lecture makes every subgroup
  busy, and a subgroup's lab makes the cohort unavailable. Clashes and daily
  load are judged per **leaf** group, which is the finest unit an actual student
  belongs to.

Travel between buildings
------------------------
A room sits in a building, and crossing campus takes time. ``travel_slots``
says how many free periods must separate a session in one building from a
session in another, for the same people. Zero — the default, and the same
building always — means back-to-back is fine. It applies to lecturers as well
as to student groups: a lecturer cannot teleport either.

Hard and soft
-------------
Conflicts, capacity, room suitability, availability and load limits are hard:
a timetable that breaks one is not a timetable. ``PreferredTime`` and
``PreferredRoom`` are preferences, so they are priced in the objective instead.
Encoding a preference as a constraint is how a timetabling model becomes
infeasible for no good reason.
"""

from __future__ import annotations

from psp.ir.dsl import add, all_of, cmp, eq, i, le, mul, num, over, p, sub, total, v
from psp.problem.spec import (
    Assumption,
    ProblemConstraint,
    ProblemObjective,
    ProblemSet,
    ProblemSpec,
    ProblemVariable,
    Scenario,
    ScenarioOverride,
)
from psp.problem.templates.base import ProblemTemplate, TemplateInput


class LectureTimetablingTemplate(ProblemTemplate):
    key = "lecture_timetabling"
    title = "Lecture timetabling"
    summary = (
        "Place every course offering into a room and a weekly time slot so that no "
        "lecturer, room or student group is double-booked, rooms suit the course, "
        "everyone is available, and daily load stays within limits."
    )
    category = "education"
    tags = ["cp-sat", "mip", "timetabling", "time-indexed"]

    def inputs(self) -> list[TemplateInput]:
        return [
            TemplateInput(key="days", label="Teaching days", kind="entities",
                          description="Days of the teaching week, in order."),
            TemplateInput(key="slots_per_day", label="Slots per day", kind="number",
                          description="Number of equal teaching periods in a day."),
            TemplateInput(key="slot_labels", label="Slot times", kind="entities", required=False,
                          description="Start time of each period, for reading the timetable."),
            TemplateInput(key="rooms", label="Rooms", kind="table",
                          description="Rooms with capacity, type and building.",
                          columns=["key", "capacity", "type", "building"]),
            TemplateInput(key="lecturers", label="Lecturers", kind="entities",
                          description="Staff who can be assigned to offerings."),
            TemplateInput(key="groups", label="Student groups", kind="table",
                          description=(
                              "Groups that attend together, with their size. A lab subgroup "
                              "names its cohort as parent."
                          ),
                          columns=["key", "size", "program", "year", "parent"]),
            TemplateInput(key="courses", label="Courses", kind="table",
                          description="Catalogue entries and the room type each needs.",
                          columns=["key", "name", "room_type"]),
            TemplateInput(key="offerings", label="Course offerings", kind="table",
                          description=(
                              "What is actually scheduled: a course, its lecturer, the groups "
                              "attending, sessions per week and session length in slots."
                          ),
                          columns=["key", "course", "lecturer", "groups",
                                   "sessions_per_week", "duration"]),
            TemplateInput(key="lecturer_unavailable", label="Lecturer unavailability",
                          kind="table", required=False,
                          description="Slots a lecturer cannot teach, as day/slot pairs.",
                          columns=["lecturer", "day", "slot"]),
            TemplateInput(key="room_unavailable", label="Room unavailability", kind="table",
                          required=False, description="Slots a room is blocked.",
                          columns=["room", "day", "slot"]),
            TemplateInput(key="group_unavailable", label="Group unavailability", kind="table",
                          required=False,
                          description="Slots a group cannot attend — fixed events, holidays.",
                          columns=["group", "day", "slot"]),
            TemplateInput(key="travel_slots", label="Travel between buildings", kind="table",
                          required=False,
                          description=(
                              "Free periods needed between sessions in two buildings. "
                              "Applied both ways; omitted pairs need none."
                          ),
                          columns=["from", "to", "slots"]),
            TemplateInput(key="max_daily_per_group", label="Maximum lectures per day",
                          kind="number", required=False, default=4,
                          description="Most sessions any group may have in one day."),
            TemplateInput(key="max_consecutive_per_group", label="Maximum consecutive",
                          kind="number", required=False, default=3,
                          description="Longest run of back-to-back slots for a group."),
            TemplateInput(key="avoid_slots", label="Discouraged times", kind="table",
                          required=False,
                          description="Penalty for placing an offering at a slot. Soft.",
                          columns=["offering", "day", "slot", "penalty"]),
            TemplateInput(key="preferred_room", label="Preferred rooms", kind="table",
                          required=False,
                          description="Penalty for NOT using the preferred room. Soft.",
                          columns=["offering", "room", "penalty"]),
        ]

    # ------------------------------------------------------------- example

    def example(self) -> dict:
        """A small but realistic computer-science department week."""
        return {
            "name": "Faculty of Engineering — CS department, semester 1",
            "days": ["Sun", "Mon", "Tue", "Wed", "Thu"],
            "slots_per_day": 5,
            "slot_labels": ["08:30", "10:15", "12:00", "13:45", "15:30"],
            "rooms": {
                "hall_a": {"capacity": 120, "type": "lecture_hall", "building": "main"},
                "hall_b": {"capacity": 80, "type": "lecture_hall", "building": "main"},
                "room_201": {"capacity": 45, "type": "classroom", "building": "main"},
                "room_202": {"capacity": 45, "type": "classroom", "building": "annex"},
                "lab_1": {"capacity": 30, "type": "computer_lab", "building": "annex"},
                "lab_2": {"capacity": 30, "type": "computer_lab", "building": "annex"},
            },
            "lecturers": ["haddad", "nasser", "aziz", "farouk", "salim"],
            "groups": {
                "cs_y2_a": {"size": 42, "program": "computer_science", "year": 2},
                "cs_y2_a1": {"size": 21, "program": "computer_science", "year": 2,
                             "parent": "cs_y2_a"},
                "cs_y2_a2": {"size": 21, "program": "computer_science", "year": 2,
                             "parent": "cs_y2_a"},
                "cs_y2_b": {"size": 38, "program": "computer_science", "year": 2},
                "cs_y2_b1": {"size": 19, "program": "computer_science", "year": 2,
                             "parent": "cs_y2_b"},
                "cs_y2_b2": {"size": 19, "program": "computer_science", "year": 2,
                             "parent": "cs_y2_b"},
                "cs_y3_a": {"size": 28, "program": "computer_science", "year": 3},
                "se_y3_a": {"size": 25, "program": "software_engineering", "year": 3},
            },
            "courses": {
                "db_systems": {"name": "Database Systems", "room_type": "lecture_hall"},
                "db_lab": {"name": "Database Systems Lab", "room_type": "computer_lab"},
                "algorithms": {"name": "Algorithms", "room_type": "lecture_hall"},
                "networks": {"name": "Computer Networks", "room_type": "lecture_hall"},
                "networks_lab": {"name": "Networks Lab", "room_type": "computer_lab"},
                "se_design": {"name": "Software Design", "room_type": "classroom"},
                "os": {"name": "Operating Systems", "room_type": "lecture_hall"},
            },
            # One course, several offerings: Database Systems is lectured once to
            # both year-2 cohorts and examined in the lab four times, once per
            # subgroup. That split is why the offering, not the course, is the
            # thing with a timetable.
            "offerings": {
                "db_y2": {"course": "db_systems", "lecturer": "haddad",
                          "groups": ["cs_y2_a", "cs_y2_b"],
                          "sessions_per_week": 2, "duration": 1},
                "dblab_a1": {"course": "db_lab", "lecturer": "haddad",
                             "groups": ["cs_y2_a1"], "sessions_per_week": 1, "duration": 2},
                "dblab_a2": {"course": "db_lab", "lecturer": "nasser",
                             "groups": ["cs_y2_a2"], "sessions_per_week": 1, "duration": 2},
                "dblab_b1": {"course": "db_lab", "lecturer": "nasser",
                             "groups": ["cs_y2_b1"], "sessions_per_week": 1, "duration": 2},
                "dblab_b2": {"course": "db_lab", "lecturer": "farouk",
                             "groups": ["cs_y2_b2"], "sessions_per_week": 1, "duration": 2},
                "algo_y2": {"course": "algorithms", "lecturer": "nasser",
                            "groups": ["cs_y2_a", "cs_y2_b"],
                            "sessions_per_week": 2, "duration": 1},
                "net_y3": {"course": "networks", "lecturer": "aziz",
                           "groups": ["cs_y3_a", "se_y3_a"],
                           "sessions_per_week": 2, "duration": 1},
                "netlab_y3": {"course": "networks_lab", "lecturer": "aziz",
                              "groups": ["cs_y3_a"], "sessions_per_week": 1, "duration": 2},
                "os_y3": {"course": "os", "lecturer": "farouk",
                          "groups": ["cs_y3_a"], "sessions_per_week": 2, "duration": 1},
                "sed_y3": {"course": "se_design", "lecturer": "salim",
                           "groups": ["se_y3_a"], "sessions_per_week": 2, "duration": 1},
            },
            "lecturer_unavailable": [
                {"lecturer": "haddad", "day": "Sun", "slot": s} for s in range(5)
            ] + [{"lecturer": "salim", "day": "Thu", "slot": 3},
                 {"lecturer": "salim", "day": "Thu", "slot": 4}],
            "room_unavailable": [
                {"room": r, "day": "Thu", "slot": s} for r in ("lab_1", "lab_2") for s in (3, 4)
            ],
            "group_unavailable": [
                {"group": g, "day": "Wed", "slot": 0} for g in ("cs_y3_a", "se_y3_a")
            ],
            # The annex is a ten-minute walk from the main building, which is
            # longer than the changeover between periods.
            "travel_slots": [{"from": "main", "to": "annex", "slots": 1}],
            "max_daily_per_group": 3,
            "max_consecutive_per_group": 2,
            # Nobody wants the last slot of the day.
            "avoid_slots": [
                {"offering": o, "day": d, "slot": 4, "penalty": 3}
                for o in ("db_y2", "algo_y2", "net_y3", "os_y3", "sed_y3")
                for d in ("Sun", "Mon", "Tue", "Wed", "Thu")
            ],
            "preferred_room": [
                {"offering": "db_y2", "room": "hall_a", "penalty": 2},
                {"offering": "net_y3", "room": "hall_b", "penalty": 2},
            ],
        }

    # --------------------------------------------------------------- build

    def build(self, data: dict) -> ProblemSpec:
        days = [str(d) for d in self.require(data, "days")]
        slots_per_day = int(self.require(data, "slots_per_day"))
        rooms = self.require(data, "rooms")
        lecturers = [str(x) for x in self.require(data, "lecturers")]
        groups = self.require(data, "groups")
        courses = self.require(data, "courses")
        offerings = self.require(data, "offerings")
        slot_labels = data.get("slot_labels") or [str(s) for s in range(slots_per_day)]

        if slots_per_day < 1:
            raise ValueError("a teaching day needs at least one slot")
        if len(slot_labels) != slots_per_day:
            raise ValueError(
                f"{len(slot_labels)} slot times were given for {slots_per_day} slots per day"
            )

        total_slots = len(days) * slots_per_day
        room_keys = list(rooms)
        group_keys = list(groups)
        offering_keys = list(offerings)

        def slot_index(day: str, slot: int) -> int:
            if day not in days:
                raise ValueError(f"'{day}' is not one of the teaching days {days}")
            if not 0 <= int(slot) < slots_per_day:
                raise ValueError(f"slot {slot} is outside the {slots_per_day} slots of a day")
            return days.index(day) * slots_per_day + int(slot)

        # ------------------------------------------------ validate the data
        for key, o in offerings.items():
            if o["course"] not in courses:
                raise ValueError(f"offering '{key}' names unknown course '{o['course']}'")
            if o["lecturer"] not in lecturers:
                raise ValueError(f"offering '{key}' names unknown lecturer '{o['lecturer']}'")
            for g in o["groups"]:
                if g not in groups:
                    raise ValueError(f"offering '{key}' names unknown student group '{g}'")
            if int(o.get("duration", 1)) > slots_per_day:
                raise ValueError(
                    f"offering '{key}' lasts {o['duration']} slots but a day has only "
                    f"{slots_per_day}; it can never be placed"
                )

        buildings = sorted({r["building"] for r in rooms.values()})
        travel = {f"{b1}|{b2}": 0.0 for b1 in buildings for b2 in buildings}
        for row in data.get("travel_slots") or []:
            for a, b in ((row["from"], row["to"]), (row["to"], row["from"])):
                if a not in buildings or b not in buildings:
                    raise ValueError(
                        f"travel time names unknown building '{a if a not in buildings else b}'"
                    )
                if a != b:
                    # Symmetric by default: walking back takes as long as walking there.
                    travel[f"{a}|{b}"] = float(row["slots"])
        if any(v < 0 for v in travel.values()):
            raise ValueError("travel time between buildings cannot be negative")
        # Gaps wider than a day can never fall inside one, so the family is
        # capped there. A travel time at or beyond the cap is still meaningful:
        # it says the same people cannot use both buildings on the same day.
        max_travel = min(
            int(max(travel.values())) if travel else 0,
            slots_per_day - 1,
        )

        max_duration = max(int(o.get("duration", 1)) for o in offerings.values())
        max_daily = int(data.get("max_daily_per_group", 4))
        max_consecutive = int(data.get("max_consecutive_per_group", 3))

        # Checks worth doing before the solver spends time on it. Counting
        # room-slots in total would miss the common real failure — plenty of
        # lecture halls, no laboratory — so demand and supply are compared per
        # room type, which is the granularity the room_type rule enforces.
        demand_by_type: dict[str, int] = {}
        for o in offerings.values():
            needed = courses[o["course"]]["room_type"]
            demand_by_type[needed] = demand_by_type.get(needed, 0) + (
                int(o.get("sessions_per_week", 1)) * int(o.get("duration", 1))
            )
        supply_by_type: dict[str, int] = {}
        for r in room_keys:
            supply_by_type[rooms[r]["type"]] = supply_by_type.get(rooms[r]["type"], 0) + 1

        for room_type, needed in sorted(demand_by_type.items()):
            available = supply_by_type.get(room_type, 0)
            if available == 0:
                raise ValueError(
                    f"{needed} room-slot(s) of type '{room_type}' are needed but the faculty "
                    f"has no room of that type; no timetable can exist"
                )
            if needed > available * total_slots:
                raise ValueError(
                    f"courses needing a '{room_type}' require {needed} room-slots but only "
                    f"{available * total_slots} exist ({available} room(s) x {total_slots} "
                    f"slots); no timetable can exist"
                )

        # ------------------------------------------------ group hierarchy
        parent_of = {g: groups[g].get("parent") for g in group_keys}
        for g, parent in parent_of.items():
            if parent is not None and parent not in groups:
                raise ValueError(f"group '{g}' names unknown parent group '{parent}'")

        def ancestors(g: str) -> list[str]:
            chain, seen, current = [], {g}, parent_of.get(g)
            while current is not None:
                if current in seen:
                    raise ValueError(f"group '{g}' is its own ancestor; the hierarchy is cyclic")
                chain.append(current)
                seen.add(current)
                current = parent_of.get(current)
            return chain

        descendants = {g: set() for g in group_keys}
        for g in group_keys:
            for a in ancestors(g):
                descendants[a].add(g)

        # A leaf is the finest group a real student belongs to, so clashes and
        # daily load are judged there rather than on a cohort that splits.
        leaves = [g for g in group_keys if not descendants[g]]

        # --------------------------------------------------- derived tables
        # Enrolment counts only the directly attending groups: a subgroup lab is
        # sized for the subgroup, not for the cohort it came from.
        enrolment = {
            key: sum(int(groups[g]["size"]) for g in o["groups"])
            for key, o in offerings.items()
        }
        teaches = {f"{key}|{o['lecturer']}": 1.0 for key, o in offerings.items()}
        attends = {
            f"{key}|{g}": 1.0 for key, o in offerings.items() for g in o["groups"]
        }
        # Busy is the transitive relation used for clashes: a cohort's lecture
        # occupies every subgroup, and a subgroup's lab occupies the cohort.
        busy: dict[str, float] = {}
        for key, o in offerings.items():
            for g in o["groups"]:
                for related in {g, *ancestors(g), *descendants[g]}:
                    busy[f"{key}|{related}"] = 1.0
        is_leaf = {g: (1.0 if g in leaves else 0.0) for g in group_keys}
        room_type_ok = {
            f"{key}|{r}": 1.0
            for key, o in offerings.items()
            for r in room_keys
            if rooms[r]["type"] == courses[o["course"]]["room_type"]
        }

        def availability(entries, subject_key, subjects):
            """Start from fully available, then knock out the blocked slots."""
            table = {f"{s}|{t}": 1.0 for s in subjects for t in range(total_slots)}
            for row in entries or []:
                subject = str(row[subject_key])
                if subject not in subjects:
                    raise ValueError(f"unavailability names unknown {subject_key} '{subject}'")
                table[f"{subject}|{slot_index(row['day'], row['slot'])}"] = 0.0
            return table

        lecturer_available = availability(
            data.get("lecturer_unavailable"), "lecturer", lecturers)
        room_available = availability(data.get("room_unavailable"), "room", room_keys)
        group_available = availability(data.get("group_unavailable"), "group", group_keys)
        # A cohort that is blocked blocks its subgroups too, so the leaf-level
        # check below sees the whole picture.
        for g in group_keys:
            for a in ancestors(g):
                for t in range(total_slots):
                    if group_available[f"{a}|{t}"] == 0.0:
                        group_available[f"{g}|{t}"] = 0.0

        time_penalty = {}
        for row in data.get("avoid_slots") or []:
            if row["offering"] not in offerings:
                raise ValueError(f"a discouraged time names unknown offering '{row['offering']}'")
            time_penalty[f"{row['offering']}|{slot_index(row['day'], row['slot'])}"] = float(
                row.get("penalty", 1))

        # A preference is a penalty on every room *except* the preferred one, so
        # the objective is zero when the wish is granted rather than merely lower.
        room_penalty = {}
        for row in data.get("preferred_room") or []:
            if row["offering"] not in offerings:
                raise ValueError(f"a room preference names unknown offering '{row['offering']}'")
            for r in room_keys:
                if r != row["room"]:
                    room_penalty[f"{row['offering']}|{r}"] = float(row.get("penalty", 1))

        parameters = [
            self.indexed("duration", ["Offerings"],
                         {k: float(o.get("duration", 1)) for k, o in offerings.items()},
                         description="Slots one session occupies", unit="slots"),
            self.indexed("sessions", ["Offerings"],
                         {k: float(o.get("sessions_per_week", 1)) for k, o in offerings.items()},
                         description="Sessions required each week"),
            self.indexed("enrolment", ["Offerings"],
                         {k: float(v_) for k, v_ in enrolment.items()},
                         description="Students attending, summed over the groups"),
            self.indexed("capacity", ["Rooms"],
                         {r: float(rooms[r]["capacity"]) for r in room_keys},
                         description="Seats in the room"),
            self.indexed("teaches", ["Offerings", "Lecturers"], teaches, default=0.0,
                         description="1 when the lecturer delivers the offering"),
            self.indexed("attends", ["Offerings", "Groups"], attends, default=0.0,
                         description="1 when the group directly attends the offering"),
            self.indexed("busy", ["Offerings", "Groups"], busy, default=0.0,
                         description="1 when the offering occupies the group, its cohort "
                                     "or its subgroups"),
            self.indexed("is_leaf", ["Groups"], is_leaf, default=0.0,
                         description="1 for the finest group an actual student belongs to"),
            self.indexed("room_type_ok", ["Offerings", "Rooms"], room_type_ok, default=0.0,
                         description="1 when the room is of the type the course needs"),
            self.indexed("lecturer_available", ["Lecturers", "Slots"], lecturer_available,
                         default=1.0, description="1 when the lecturer can teach at that slot"),
            self.indexed("room_available", ["Rooms", "Slots"], room_available, default=1.0,
                         description="1 when the room is open at that slot"),
            self.indexed("group_available", ["Groups", "Slots"], group_available, default=1.0,
                         description="1 when the group is free at that slot"),
            self.indexed("day_of", ["Slots"],
                         {str(t): float(t // slots_per_day) for t in range(total_slots)},
                         description="Which day a slot falls in"),
            self.indexed("slot_in_day", ["Slots"],
                         {str(t): float(t % slots_per_day) for t in range(total_slots)},
                         description="Position of the slot within its day"),
            self.indexed("slots_per_day", [], {"": float(slots_per_day)},
                         description="Teaching periods in a day"),
            self.indexed("max_daily", [], {"": float(max_daily)},
                         description="Most sessions a group may have in one day"),
            self.indexed("max_consecutive", [], {"": float(max_consecutive)},
                         description="Longest permitted run of back-to-back slots"),
            self.indexed("in_building", ["Rooms", "Buildings"],
                         {f"{r}|{rooms[r]['building']}": 1.0 for r in room_keys}, default=0.0,
                         description="1 when the room is in that building"),
            self.indexed("travel", ["Buildings", "Buildings"], travel, default=0.0,
                         description="Free periods needed to cross between two buildings",
                         unit="slots"),
            self.indexed("time_penalty", ["Offerings", "Slots"], time_penalty, default=0.0,
                         description="Cost of placing an offering at a discouraged slot"),
            self.indexed("room_penalty", ["Offerings", "Rooms"], room_penalty, default=0.0,
                         description="Cost of not using the preferred room"),
        ]

        # ``in_use(t)`` is the sum of starts at t, t-1, ... t-duration+1. The
        # Offsets set keeps that a small local scan instead of a sweep over the
        # whole week for every conflict row.
        def occupies(offset: str, start) -> tuple:
            """Guards selecting the slots a session starting at ``start`` runs for.

            Returned as a tuple so callers splat it into their own ``all_of``:
            the range check has to be evaluated before the caller reads a table
            at ``start + offset``, and ``all_of`` short-circuits in order.
            """
            return (
                cmp("lt", i(offset), p("duration", i("o"))),
                cmp("le", add(start, i(offset)), num(total_slots - 1)),
            )

        def covers(offset: str, at) -> object:
            """Guard: offset k is inside the session length and t-k is a real slot."""
            return all_of(
                cmp("lt", i(offset), p("duration", i("o"))),
                cmp("ge", sub(at, i(offset)), num(0)),
            )

        constraints = [
            ProblemConstraint(
                name="sessions_scheduled",
                statement="Every offering is given exactly the number of sessions it requires.",
                category="operational",
                rationale="An offering short of a session has not been timetabled.",
                forall=over(o="Offerings"),
                rel=eq(
                    total(v("place", i("o"), i("r"), i("t")), r="Rooms", t="Slots"),
                    p("sessions", i("o")),
                ),
            ),
            ProblemConstraint(
                name="fits_within_day",
                statement="A session may not start so late that it would run past the "
                          "end of the day.",
                category="physical",
                rationale="Slots are numbered across the week, so without this a session "
                          "would silently continue into the next morning.",
                rel=eq(
                    total(
                        v("place", i("o"), i("r"), i("t")),
                        where=cmp("gt", add(p("slot_in_day", i("t")), p("duration", i("o"))),
                                  p("slots_per_day")),
                        o="Offerings", r="Rooms", t="Slots",
                    ),
                    num(0),
                ),
            ),
            ProblemConstraint(
                name="room_capacity",
                statement="A room must seat everyone enrolled in the offering placed in it.",
                category="physical",
                forall=[],
                rel=eq(
                    total(
                        v("place", i("o"), i("r"), i("t")),
                        where=cmp("lt", p("capacity", i("r")), p("enrolment", i("o"))),
                        o="Offerings", r="Rooms", t="Slots",
                    ),
                    num(0),
                ),
            ),
            ProblemConstraint(
                name="room_type_required",
                statement="A course must be taught in a room of the type it requires — "
                          "a laboratory course needs a laboratory.",
                category="regulatory",
                rel=eq(
                    total(
                        v("place", i("o"), i("r"), i("t")),
                        where=cmp("eq", p("room_type_ok", i("o"), i("r")), num(0)),
                        o="Offerings", r="Rooms", t="Slots",
                    ),
                    num(0),
                ),
            ),
            ProblemConstraint(
                name="lecturer_availability",
                statement="A lecturer is only scheduled at times they are available, for "
                          "every period the session runs.",
                category="policy",
                rationale="A session that starts in an available period can still run on "
                          "into an unavailable one.",
                rel=eq(
                    total(
                        v("place", i("o"), i("r"), i("t")),
                        where=all_of(
                            cmp("eq", p("teaches", i("o"), i("l")), num(1)),
                            *occupies("k", i("t")),
                            cmp("eq", p("lecturer_available", i("l"), add(i("t"), i("k"))),
                                num(0)),
                        ),
                        o="Offerings", r="Rooms", t="Slots", l="Lecturers", k="Offsets",
                    ),
                    num(0),
                ),
            ),
            ProblemConstraint(
                name="room_availability",
                statement="A room is only used at times it is open, for every period the "
                          "session runs.",
                category="physical",
                rationale="A two-period lab must not start before a closure and run through it.",
                rel=eq(
                    total(
                        v("place", i("o"), i("r"), i("t")),
                        where=all_of(
                            *occupies("k", i("t")),
                            cmp("eq", p("room_available", i("r"), add(i("t"), i("k"))), num(0)),
                        ),
                        o="Offerings", r="Rooms", t="Slots", k="Offsets",
                    ),
                    num(0),
                ),
            ),
            ProblemConstraint(
                name="group_availability",
                statement="A group is not taught during its blocked periods — fixed events, "
                          "seminars and holidays — for any period the session runs.",
                category="policy",
                rel=eq(
                    total(
                        v("place", i("o"), i("r"), i("t")),
                        where=all_of(
                            cmp("eq", p("busy", i("o"), i("g")), num(1)),
                            *occupies("k", i("t")),
                            cmp("eq", p("group_available", i("g"), add(i("t"), i("k"))), num(0)),
                        ),
                        o="Offerings", r="Rooms", t="Slots", g="Groups", k="Offsets",
                    ),
                    num(0),
                ),
            ),
            ProblemConstraint(
                name="lecturer_no_overlap",
                statement="A lecturer cannot teach two sessions at the same time.",
                category="physical",
                rationale="Counts every session still running at that slot, not only those "
                          "starting there.",
                forall=over(l="Lecturers", t="Slots"),
                rel=le(
                    total(
                        v("place", i("o"), i("r"), sub(i("t"), i("k"))),
                        where=all_of(
                            cmp("eq", p("teaches", i("o"), i("l")), num(1)),
                            covers("k", i("t")),
                        ),
                        o="Offerings", r="Rooms", k="Offsets",
                    ),
                    num(1),
                ),
            ),
            ProblemConstraint(
                name="room_no_overlap",
                statement="A room cannot host two sessions at the same time.",
                category="physical",
                forall=over(r="Rooms", t="Slots"),
                rel=le(
                    total(
                        v("place", i("o"), i("r"), sub(i("t"), i("k"))),
                        where=covers("k", i("t")),
                        o="Offerings", k="Offsets",
                    ),
                    num(1),
                ),
            ),
            ProblemConstraint(
                name="group_no_overlap",
                statement="A student group cannot be in two places at once.",
                category="physical",
                forall=over(g="Groups", t="Slots"),
                where=cmp("eq", p("is_leaf", i("g")), num(1)),
                rel=le(
                    total(
                        v("place", i("o"), i("r"), sub(i("t"), i("k"))),
                        where=all_of(
                            cmp("eq", p("busy", i("o"), i("g")), num(1)),
                            covers("k", i("t")),
                        ),
                        o="Offerings", r="Rooms", k="Offsets",
                    ),
                    num(1),
                ),
            ),
            ProblemConstraint(
                name="max_daily_lectures",
                statement="No group has more than the permitted number of sessions in a day.",
                category="policy",
                rationale="Protects against a timetable that is feasible but exhausting.",
                forall=over(g="Groups", d="Days"),
                where=cmp("eq", p("is_leaf", i("g")), num(1)),
                rel=le(
                    total(
                        v("place", i("o"), i("r"), i("t")),
                        where=all_of(
                            cmp("eq", p("busy", i("o"), i("g")), num(1)),
                            cmp("eq", p("day_of", i("t")), i("d")),
                        ),
                        o="Offerings", r="Rooms", t="Slots",
                    ),
                    p("max_daily"),
                ),
            ),
            ProblemConstraint(
                name="max_consecutive_lectures",
                statement="A group never has a longer unbroken run of teaching than allowed, "
                          "which is what guarantees them a break.",
                category="policy",
                rationale="Every window of max_consecutive+1 slots inside a day may hold at "
                          "most max_consecutive of that group's sessions.",
                forall=over(g="Groups", t="Slots"),
                where=all_of(
                    cmp("eq", p("is_leaf", i("g")), num(1)),
                    cmp("le", add(p("slot_in_day", i("t")), p("max_consecutive")),
                        sub(p("slots_per_day"), num(1))),
                ),
                rel=le(
                    total(
                        v("place", i("o"), i("r"), sub(add(i("t"), i("j")), i("k"))),
                        where=all_of(
                            cmp("eq", p("busy", i("o"), i("g")), num(1)),
                            cmp("le", i("j"), p("max_consecutive")),
                            covers("k", add(i("t"), i("j"))),
                        ),
                        o="Offerings", r="Rooms", j="Window", k="Offsets",
                    ),
                    p("max_consecutive"),
                ),
            ),
        ]

        # Crossing campus is only a constraint when it actually takes a period.
        # With everything walkable the family adds nothing but rows, so it is
        # left out entirely rather than generated and trivially satisfied.
        if max_travel >= 1:
            def elsewhere(who: str, relation: str, at, building: str):
                """Sessions occupying ``who`` at ``at``, in ``building``."""
                return total(
                    v("place", i("o"), i("r"), sub(at, i("k"))),
                    where=all_of(
                        cmp("eq", p(relation, i("o"), i(who)), num(1)),
                        cmp("eq", p("in_building", i("r"), i(building)), num(1)),
                        covers("k", at),
                    ),
                    o="Offerings", r="Rooms", k="Offsets",
                )

            # The range guard must come before day_of[t + d] is read, or the
            # lookup runs off the end of the week. `all_of` short-circuits, so
            # ordering these is what keeps it safe.
            def crossing(who: str) -> object:
                return all_of(
                    cmp("ge", p("travel", i("b1"), i("b2")), i("d")),
                    cmp("le", add(i("t"), i("d")), num(total_slots - 1)),
                    cmp("eq", p("day_of", i("t")), p("day_of", add(i("t"), i("d")))),
                )

            for who, relation, subject in (
                ("g", "busy", "student group"),
                ("l", "teaches", "lecturer"),
            ):
                guards = [crossing(who)]
                if who == "g":
                    guards.append(cmp("eq", p("is_leaf", i("g")), num(1)))
                bindings = {who: "Groups" if who == "g" else "Lecturers"}
                constraints.append(
                    ProblemConstraint(
                        name=f"{'group' if who == 'g' else 'lecturer'}_travel_time",
                        statement=(
                            f"A {subject} is given enough time to cross between buildings: "
                            "no session in one building immediately after a session in "
                            "another that is further away than the gap allows."
                        ),
                        category="physical",
                        rationale="Two sessions closer together than the walk between their "
                                  "buildings cannot both be attended.",
                        forall=over(t="Slots", d="Gaps", b1="Buildings", b2="Buildings",
                                    **bindings),
                        where=all_of(*guards),
                        rel=le(
                            add(
                                elsewhere(who, relation, i("t"), "b1"),
                                elsewhere(who, relation, add(i("t"), i("d")), "b2"),
                            ),
                            num(1),
                        ),
                    )
                )

        readable_slots = {
            str(d * slots_per_day + s): f"{days[d]} {slot_labels[s]}"
            for d in range(len(days))
            for s in range(slots_per_day)
        }

        return ProblemSpec(
            key=data.get("key", "lecture_timetabling"),
            name=data.get("name", "Lecture timetabling"),
            problem_type="lecture_timetabling",
            description=data.get("description", self.summary),
            template_key=self.key,
            template_inputs=data,
            sets=[
                ProblemSet(name="Offerings", elements=offering_keys, entity_type="course_offering",
                           description="What is scheduled: a course, lecturer and groups together"),
                ProblemSet(name="Rooms", elements=room_keys, entity_type="room",
                           description="Teaching rooms"),
                # Slots and days must be numeric for the compiler to do arithmetic
                # on them, and must read as times for anyone looking at the
                # answer. The label map is what reconciles the two.
                ProblemSet(name="Slots", kind="int",
                           elements=[str(t) for t in range(total_slots)],
                           description=f"{len(days)} days x {slots_per_day} periods, numbered "
                                       "across the week",
                           labels=readable_slots),
                ProblemSet(name="Days", kind="int", elements=[str(d) for d in range(len(days))],
                           description="Teaching days: " + ", ".join(days),
                           labels={str(d): day for d, day in enumerate(days)}),
                ProblemSet(name="Lecturers", elements=lecturers, entity_type="lecturer",
                           description="Teaching staff"),
                ProblemSet(name="Groups", elements=group_keys, entity_type="student_group",
                           description="Student groups that move together"),
                ProblemSet(name="Buildings", elements=buildings, entity_type="building",
                           description="Buildings the rooms sit in"),
                *([ProblemSet(name="Gaps", kind="int",
                              elements=[str(d) for d in range(1, max_travel + 1)],
                              description="Period gaps a building change may need")]
                  if max_travel >= 1 else []),
                ProblemSet(name="Offsets", kind="int",
                           elements=[str(k) for k in range(max_duration)],
                           description="Positions within a session, for occupancy counting"),
                ProblemSet(name="Window", kind="int",
                           elements=[str(j) for j in range(max_consecutive + 1)],
                           description="Slots in a consecutive-run window"),
            ],
            parameters=parameters,
            variables=[
                ProblemVariable(
                    name="place", index_sets=["Offerings", "Rooms", "Slots"], kind="binary",
                    lb=0.0, ub=1.0,
                    description="A session of this offering starts in this room at this slot",
                    decision_meaning="Hold this session in this room at this time",
                )
            ],
            constraints=constraints,
            objectives=[
                ProblemObjective(
                    name="preference_cost",
                    statement="Grant as many timing and room preferences as the hard "
                              "constraints allow.",
                    sense="minimize",
                    expr=total(
                        mul(
                            add(p("time_penalty", i("o"), i("t")),
                                p("room_penalty", i("o"), i("r"))),
                            v("place", i("o"), i("r"), i("t")),
                        ),
                        o="Offerings", r="Rooms", t="Slots",
                    ),
                    unit="penalty points",
                )
            ],
            assumptions=[
                Assumption(
                    key="offering_is_the_unit",
                    statement="The course offering is scheduled, not the course.",
                    rationale="One course may run several times with different lecturers, "
                              "groups and room needs; only the offering has a timetable.",
                    affects=["place", "sessions_scheduled"],
                ),
                Assumption(
                    key="sessions_interchangeable",
                    statement="The weekly sessions of an offering are interchangeable.",
                    rationale="No lecture-before-tutorial ordering is modelled; add a "
                              "precedence rule if the teaching order matters.",
                    affects=["sessions_scheduled"],
                ),
                Assumption(
                    key="whole_group_attends",
                    statement="Every student in a group attends every session of its offerings.",
                    rationale="Room capacity is checked against the groups directly attending, "
                              "so a subgroup lab is sized for the subgroup.",
                    affects=["room_capacity"],
                ),
                Assumption(
                    key="clashes_judged_at_the_leaf",
                    statement="Clashes and daily load are judged for the finest group a student "
                              "belongs to, counting the cohort's lectures as well as the "
                              "subgroup's own sessions.",
                    rationale="Two subgroups of one cohort may sit different labs at the same "
                              "time; neither may sit one while the cohort has a lecture.",
                    affects=["group_no_overlap", "max_daily_lectures",
                             "max_consecutive_lectures"],
                ),
                Assumption(
                    key="uniform_slots",
                    statement=f"The day is {slots_per_day} periods of equal length, and a "
                              "session occupies whole consecutive periods.",
                    affects=["Slots", "fits_within_day"],
                ),
                Assumption(
                    key="travel_is_between_buildings_only",
                    statement=(
                        "Travel time depends only on the pair of buildings, and is the same "
                        "in both directions."
                        if max_travel >= 1
                        else "Every room is reachable from every other within the changeover "
                             "between periods, so travel time is not modelled."
                    ),
                    rationale=(
                        "Distances within a building, and the difference between walking up "
                        "a hill and down it, are not distinguished."
                        if max_travel >= 1
                        else "Declare travel_slots if the campus is spread out."
                    ),
                    affects=(["group_travel_time", "lecturer_travel_time"]
                             if max_travel >= 1 else ["group_no_overlap"]),
                ),
                Assumption(
                    key="break_from_consecutive_limit",
                    statement="A minimum break is enforced indirectly, by capping the longest "
                              f"unbroken run at {max_consecutive} slots.",
                    rationale="A separate minimum-break rule would be redundant with this cap.",
                    affects=["max_consecutive_lectures"],
                ),
                Assumption(
                    key="preferences_are_soft",
                    statement="Preferred times and rooms are priced, not enforced.",
                    rationale="Encoding a preference as a hard constraint is the usual cause "
                              "of a timetable that is reported infeasible for no real reason.",
                    affects=["preference_cost"],
                ),
            ],
            scenarios=[
                Scenario(
                    key="lab_closed", name="Computer labs closed all week",
                    description="Both laboratories are withdrawn — the laboratory courses "
                                "then have nowhere to go.",
                    overrides=[
                        ScenarioOverride(parameter="room_available", index=[r, str(t)], value=0.0)
                        for r in room_keys if rooms[r]["type"] == "computer_lab"
                        for t in range(total_slots)
                    ],
                ),
                Scenario(
                    key="tighter_days", name="At most two sessions a day",
                    description="A stricter daily cap on every group.",
                    overrides=[ScenarioOverride(parameter="max_daily", index=[], value=2.0)],
                ),
                *([Scenario(
                    key="distant_annex", name="Annex moved further away",
                    description="Crossing to the annex costs a second free period.",
                    overrides=[
                        ScenarioOverride(parameter="travel", index=[b1, b2],
                                         value=float(max_travel + 1))
                        for b1 in buildings for b2 in buildings
                        if travel[f"{b1}|{b2}"] >= 1
                    ],
                )] if max_travel >= 1 else []),
                Scenario(
                    key="four_day_week", name="Thursday dropped",
                    description="No teaching on the last day of the week.",
                    overrides=[
                        ScenarioOverride(parameter="room_available", index=[r, str(t)], value=0.0)
                        for r in room_keys
                        for t in range((len(days) - 1) * slots_per_day, total_slots)
                    ],
                ),
            ],
            metadata={
                "days": days,
                "slot_labels": slot_labels,
                "slots_per_day": slots_per_day,
                "slot_names": readable_slots,
                "rooms": rooms,
                "courses": courses,
                "offerings": offerings,
                "groups": groups,
            },
        )
