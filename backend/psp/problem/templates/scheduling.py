"""Scheduling: place tasks in time under precedence and resource limits.

The formulation is time-indexed — one binary per (task, possible start time) —
rather than interval-based. That costs variables, but it keeps the model inside
the platform's linear IR, so it can be solved by CP-SAT *or* by a MIP engine,
and every constraint stays readable and explainable. Interval variables would
tie the model to one solver, which is exactly what the IR exists to avoid.
"""

from __future__ import annotations

from psp.ir.dsl import add, all_of, cmp, eq, ge, i, le, mul, num, over, p, total, v
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


class SchedulingTemplate(ProblemTemplate):
    key = "scheduling"
    title = "Scheduling"
    summary = (
        "Decide when each task starts so that precedence is respected, a shared "
        "resource is never over-subscribed, and the schedule finishes as early as possible."
    )
    category = "planning"
    tags = ["cp-sat", "mip", "time-indexed", "makespan"]

    def inputs(self) -> list[TemplateInput]:
        return [
            TemplateInput(key="tasks", label="Tasks", kind="entities",
                          description="The work to be scheduled."),
            TemplateInput(key="duration", label="Duration", kind="table",
                          description="How many periods each task takes.",
                          columns=["task", "duration"]),
            TemplateInput(key="horizon", label="Horizon", kind="number",
                          description="Number of periods available."),
            TemplateInput(key="resource_usage", label="Resource usage", kind="table",
                          required=False,
                          description=(
                              "Units of the shared resource each task occupies while running."
                          ),
                          columns=["task", "usage"]),
            TemplateInput(key="resource_capacity", label="Resource capacity", kind="number",
                          required=False,
                          description="Shared-resource units available each period.",
                          default=1),
            TemplateInput(key="precedence", label="Precedence", kind="table", required=False,
                          description=(
                              "Pairs where the first task must finish before the second starts."
                          ),
                          columns=["before", "after"]),
            TemplateInput(key="release", label="Earliest start", kind="table", required=False,
                          description="Period before which a task may not start.",
                          columns=["task", "release"]),
            TemplateInput(key="deadline", label="Latest finish", kind="table", required=False,
                          description="Period by which a task must be complete.",
                          columns=["task", "deadline"]),
        ]

    def example(self) -> dict:
        return {
            "name": "Runway repair sequence",
            "tasks": ["survey", "clear_debris", "resurface", "mark_lines", "inspect"],
            "duration": {
                "survey": 2, "clear_debris": 3, "resurface": 4,
                "mark_lines": 2, "inspect": 1,
            },
            "horizon": 16,
            "resource_usage": {
                "survey": 1, "clear_debris": 2, "resurface": 2,
                "mark_lines": 1, "inspect": 1,
            },
            "resource_capacity": 2,
            "precedence": [
                ["survey", "clear_debris"],
                ["clear_debris", "resurface"],
                ["resurface", "mark_lines"],
                ["mark_lines", "inspect"],
            ],
            "release": {"survey": 0},
            "deadline": {"inspect": 16},
        }

    def build(self, data: dict) -> ProblemSpec:
        tasks = [str(t) for t in self.require(data, "tasks")]
        durations = {str(k): float(val) for k, val in self.require(data, "duration").items()}
        horizon = int(self.require(data, "horizon"))
        capacity = float(data.get("resource_capacity", 1))
        precedence = data.get("precedence") or []
        release = data.get("release") or {}
        deadline = data.get("deadline") or {}

        longest = max(durations.values()) if durations else 0
        if longest > horizon:
            raise ValueError(
                f"horizon {horizon} is shorter than the longest task ({longest:g} periods); "
                "no schedule can exist"
            )

        precedence_pairs = {
            f"{str(before)}|{str(after)}": 1.0 for before, after in precedence
        }

        parameters = [
            self.indexed("duration", ["Tasks"], durations,
                         description="Periods each task occupies", unit="periods"),
            self.indexed("usage", ["Tasks"], data.get("resource_usage") or {},
                         description="Shared-resource units held while running", default=1.0),
            self.indexed("capacity", [], {"": capacity},
                         description="Shared-resource units available per period"),
            self.indexed("precedes", ["Tasks", "Tasks"], precedence_pairs,
                         description="1 when the first task must finish before the second starts",
                         default=0.0),
            self.indexed("release", ["Tasks"], release,
                         description="Earliest permitted start period", default=0.0),
            self.indexed("deadline", ["Tasks"], deadline,
                         description="Latest permitted finish period", default=float(horizon)),
        ]

        # start[j,t] == 1 means task j begins at the start of period t. "finish time"
        # and "start time" are therefore linear functions of the start indicators,
        # which is what keeps precedence and makespan expressible without big-M.
        start_time = lambda task_index: total(  # noqa: E731 - reads better inline
            mul(i("t"), v("start", i(task_index), i("t"))), t="Times"
        )

        constraints = [
            ProblemConstraint(
                name="start_once",
                statement="Every task starts exactly once.",
                category="modelling",
                rationale="Tasks are neither dropped nor pre-empted in this formulation.",
                forall=over(j="Tasks"),
                rel=eq(total(v("start", i("j"), i("t")), t="Times"), num(1)),
            ),
            ProblemConstraint(
                name="fit_in_horizon",
                statement="A task cannot start so late that it would run past the horizon.",
                category="physical",
                forall=over(j="Tasks", t="Times"),
                where=cmp("gt", add(i("t"), p("duration", i("j"))), num(horizon)),
                rel=le(v("start", i("j"), i("t")), num(0)),
            ),
            ProblemConstraint(
                name="respect_release",
                statement="A task may not start before its earliest permitted period.",
                category="operational",
                forall=over(j="Tasks", t="Times"),
                where=cmp("lt", i("t"), p("release", i("j"))),
                rel=le(v("start", i("j"), i("t")), num(0)),
            ),
            ProblemConstraint(
                name="respect_deadline",
                statement="A task must be complete by its deadline.",
                category="regulatory",
                forall=over(j="Tasks", t="Times"),
                where=cmp("gt", add(i("t"), p("duration", i("j"))), p("deadline", i("j"))),
                rel=le(v("start", i("j"), i("t")), num(0)),
            ),
            ProblemConstraint(
                name="resource_capacity",
                statement=(
                    "In every period, the tasks running at that moment do not together "
                    "exceed the available resource."
                ),
                category="physical",
                rationale=(
                    "A task started at s is running during period t whenever "
                    "s <= t < s + duration."
                ),
                forall=over(t="Times"),
                rel=le(
                    total(
                        mul(p("usage", i("j")), v("start", i("j"), i("s"))),
                        where=all_of(
                            cmp("le", i("s"), i("t")),
                            cmp("gt", add(i("s"), p("duration", i("j"))), i("t")),
                        ),
                        j="Tasks", s="Times",
                    ),
                    p("capacity"),
                ),
            ),
            ProblemConstraint(
                name="precedence",
                statement="A task cannot start before every task it depends on has finished.",
                category="operational",
                forall=over(j="Tasks", k="Tasks"),
                where=cmp("eq", p("precedes", i("j"), i("k")), num(1)),
                rel=ge(
                    start_time("k"),
                    add(start_time("j"), p("duration", i("j"))),
                ),
            ),
            ProblemConstraint(
                name="makespan_covers_all",
                statement="The makespan is at least the finish time of every task.",
                category="modelling",
                forall=over(j="Tasks"),
                rel=ge(v("makespan"), add(start_time("j"), p("duration", i("j")))),
            ),
        ]

        return ProblemSpec(
            key=data.get("key", "scheduling"),
            name=data.get("name", "Scheduling"),
            problem_type="scheduling",
            description=data.get("description", self.summary),
            template_key=self.key,
            template_inputs=data,
            sets=[
                ProblemSet(name="Tasks", elements=tasks, entity_type="task",
                           description="Tasks to schedule"),
                ProblemSet(name="Times", kind="int",
                           elements=[str(t) for t in range(horizon)],
                           description="Planning periods"),
            ],
            parameters=parameters,
            variables=[
                ProblemVariable(
                    name="start", index_sets=["Tasks", "Times"], kind="binary", lb=0.0, ub=1.0,
                    description="Whether a task begins in a given period",
                    decision_meaning="Begin this task at this period",
                ),
                ProblemVariable(
                    name="makespan", kind="integer", lb=0.0, ub=float(horizon),
                    description="Period at which the last task finishes",
                    decision_meaning="When the whole schedule completes",
                ),
            ],
            constraints=constraints,
            objectives=[
                ProblemObjective(
                    name="makespan", statement="Finish the whole schedule as early as possible.",
                    sense="minimize", expr=v("makespan"), unit="periods",
                )
            ],
            assumptions=[
                Assumption(
                    key="no_preemption",
                    statement="Once started, a task runs to completion without interruption.",
                    affects=["start_once", "resource_capacity"],
                ),
                Assumption(
                    key="discrete_time",
                    statement=f"Time is modelled as {horizon} equal, indivisible periods.",
                    rationale="A task can only start on a period boundary.",
                    affects=["Times"],
                ),
                Assumption(
                    key="single_resource",
                    statement="One shared renewable resource constrains concurrency.",
                    rationale="Multiple distinct resources need the multi-resource variant.",
                    affects=["resource_capacity"],
                ),
            ],
            scenarios=[
                Scenario(
                    key="tight_resource", name="Resource halved",
                    description="Only half the usual capacity is available each period.",
                    overrides=[ScenarioOverride(parameter="capacity", index=[], scale=0.5)],
                ),
                Scenario(
                    key="slow_work", name="Durations up 25%",
                    description="Every task takes a quarter longer than planned.",
                    overrides=[
                        ScenarioOverride(parameter="duration", index=[t], scale=1.25)
                        for t in tasks
                    ],
                ),
            ],
        )
