"""Assignment: match agents to tasks at least total cost."""

from __future__ import annotations

from psp.ir.dsl import eq, i, le, mul, over, p, total, v
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


class AssignmentTemplate(ProblemTemplate):
    key = "assignment"
    title = "Assignment"
    summary = (
        "Match agents to tasks so that every task is covered, no agent is "
        "overloaded, and total cost is as low as possible."
    )
    category = "allocation"
    tags = ["mip", "matching", "rostering"]

    def inputs(self) -> list[TemplateInput]:
        return [
            TemplateInput(key="agents", label="Agents", kind="entities",
                          description="People, crews, teams or units that can be assigned."),
            TemplateInput(key="tasks", label="Tasks", kind="entities",
                          description="Work items that need covering."),
            TemplateInput(key="cost", label="Assignment cost", kind="table",
                          description=(
                              "Cost of assigning each agent to each task. Omit a pair to forbid "
                              "it."
                          ),
                          columns=["agent", "task", "cost"]),
            TemplateInput(key="capacity", label="Agent capacity", kind="table", required=False,
                          description="Maximum tasks per agent (default 1).",
                          columns=["agent", "capacity"]),
            TemplateInput(key="required", label="Assignments per task", kind="table",
                          required=False,
                          description="How many agents each task needs (default 1).",
                          columns=["task", "required"]),
            TemplateInput(key="qualified", label="Qualification", kind="table", required=False,
                          description="1 if the agent may perform the task, 0 if not (default 1).",
                          columns=["agent", "task", "qualified"]),
        ]

    def example(self) -> dict:
        return {
            "name": "Incident response crew assignment",
            "agents": ["crew_alpha", "crew_bravo", "crew_charlie", "crew_delta"],
            "tasks": ["flood_zone_a", "flood_zone_b", "supply_depot", "field_clinic"],
            "cost": {
                "crew_alpha|flood_zone_a": 4, "crew_alpha|flood_zone_b": 9,
                "crew_alpha|supply_depot": 7, "crew_alpha|field_clinic": 8,
                "crew_bravo|flood_zone_a": 6, "crew_bravo|flood_zone_b": 4,
                "crew_bravo|supply_depot": 3, "crew_bravo|field_clinic": 7,
                "crew_charlie|flood_zone_a": 8, "crew_charlie|flood_zone_b": 5,
                "crew_charlie|supply_depot": 9, "crew_charlie|field_clinic": 4,
                "crew_delta|flood_zone_a": 7, "crew_delta|flood_zone_b": 6,
                "crew_delta|supply_depot": 5, "crew_delta|field_clinic": 6,
            },
            "capacity": {"crew_alpha": 1, "crew_bravo": 2, "crew_charlie": 1, "crew_delta": 1},
            "required": {
                "flood_zone_a": 1, "flood_zone_b": 1,
                "supply_depot": 1, "field_clinic": 1,
            },
            "qualified": {"crew_delta|field_clinic": 0},
        }

    def build(self, data: dict) -> ProblemSpec:
        agents = [str(a) for a in self.require(data, "agents")]
        tasks = [str(t) for t in self.require(data, "tasks")]

        parameters = [
            self.indexed("cost", ["Agents", "Tasks"], self.require(data, "cost"),
                         description="Cost of an agent covering a task",
                         default=float(data.get("forbidden_cost", 1e6))),
            self.indexed("capacity", ["Agents"], data.get("capacity") or {},
                         description="Maximum tasks an agent may take", default=1.0),
            self.indexed("required", ["Tasks"], data.get("required") or {},
                         description="Number of agents a task needs", default=1.0),
            self.indexed("qualified", ["Agents", "Tasks"], data.get("qualified") or {},
                         description="Whether the agent may perform the task", default=1.0),
        ]

        constraints = [
            ProblemConstraint(
                name="cover_task",
                statement="Each task receives exactly the number of agents it requires.",
                category="operational",
                rationale="An uncovered task is an unmet obligation, not a saving.",
                forall=over(t="Tasks"),
                rel=eq(
                    total(v("assign", i("a"), i("t")), a="Agents"),
                    p("required", i("t")),
                ),
            ),
            ProblemConstraint(
                name="agent_capacity",
                statement="No agent is given more tasks than they can carry.",
                category="physical",
                forall=over(a="Agents"),
                rel=le(
                    total(v("assign", i("a"), i("t")), t="Tasks"),
                    p("capacity", i("a")),
                ),
            ),
            ProblemConstraint(
                name="only_qualified",
                statement="An agent may only be assigned to a task they are qualified for.",
                category="regulatory",
                rationale="Qualification is a hard gate, never traded against cost.",
                forall=over(a="Agents", t="Tasks"),
                rel=le(v("assign", i("a"), i("t")), p("qualified", i("a"), i("t"))),
            ),
        ]

        return ProblemSpec(
            key=data.get("key", "assignment"),
            name=data.get("name", "Assignment"),
            problem_type="assignment",
            description=data.get("description", self.summary),
            template_key=self.key,
            template_inputs=data,
            sets=[
                ProblemSet(name="Agents", elements=agents, entity_type="agent",
                           description="Assignable agents"),
                ProblemSet(name="Tasks", elements=tasks, entity_type="task",
                           description="Tasks to be covered"),
            ],
            parameters=parameters,
            variables=[
                ProblemVariable(
                    name="assign", index_sets=["Agents", "Tasks"], kind="binary",
                    lb=0.0, ub=1.0,
                    description="Whether an agent is assigned to a task",
                    decision_meaning="Assign this agent to this task",
                )
            ],
            constraints=constraints,
            objectives=[
                ProblemObjective(
                    name="total_cost", statement="Minimise the total cost of the assignment.",
                    sense="minimize",
                    expr=total(
                        mul(p("cost", i("a"), i("t")), v("assign", i("a"), i("t"))),
                        a="Agents", t="Tasks",
                    ),
                    unit="cost units",
                )
            ],
            assumptions=[
                Assumption(
                    key="independent_costs",
                    statement="The cost of an assignment does not depend on other assignments.",
                    rationale="No coordination savings or interference effects are modelled.",
                    affects=["total_cost"],
                ),
                Assumption(
                    key="forbidden_as_penalty",
                    statement=(
                        "Pairs with no stated cost are treated as allowed but very expensive."
                    ),
                    rationale=(
                        "This keeps the model feasible when data is incomplete; use the "
                        "qualification table to forbid a pair outright."
                    ),
                    affects=["cost", "only_qualified"],
                ),
            ],
            scenarios=[
                Scenario(
                    key="one_agent_lost", name="Largest agent unavailable",
                    description="The highest-capacity agent is withdrawn from the pool.",
                    overrides=[
                        ScenarioOverride(
                            parameter="capacity",
                            index=[_largest(data, agents)], value=0.0,
                        )
                    ],
                ),
            ],
        )


def _largest(data: dict, agents: list[str]) -> str:
    capacity = data.get("capacity") or {}
    return max(agents, key=lambda a: float(capacity.get(a, 1.0)))
