"""Resource allocation: choose activity levels under shared capacity limits."""

from __future__ import annotations

from psp.ir.dsl import ge, i, le, mul, over, p, total, v
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


class ResourceAllocationTemplate(ProblemTemplate):
    key = "resource_allocation"
    title = "Resource allocation"
    summary = (
        "Decide how much of each activity to run when they compete for the same "
        "limited resources, maximising total value."
    )
    category = "planning"
    tags = ["lp", "capacity", "portfolio"]

    def inputs(self) -> list[TemplateInput]:
        return [
            TemplateInput(key="activities", label="Activities", kind="entities",
                          description="The things you can choose to do, and how much value each unit yields."),
            TemplateInput(key="resources", label="Resources", kind="entities",
                          description="The limited resources the activities consume."),
            TemplateInput(key="value", label="Value per unit", kind="table",
                          description="Value produced by one unit of each activity.",
                          columns=["activity", "value"]),
            TemplateInput(key="capacity", label="Resource capacity", kind="table",
                          description="How much of each resource is available.",
                          columns=["resource", "capacity"]),
            TemplateInput(key="usage", label="Resource usage", kind="table",
                          description="Units of each resource consumed per unit of activity.",
                          columns=["activity", "resource", "usage"]),
            TemplateInput(key="max_level", label="Maximum activity level", kind="table",
                          description="Optional cap on each activity.", required=False,
                          columns=["activity", "max"]),
            TemplateInput(key="min_level", label="Minimum activity level", kind="table",
                          description="Optional commitment already made for each activity.",
                          required=False, columns=["activity", "min"]),
            TemplateInput(key="integral", label="Whole units only", kind="choice",
                          description="Whether activity levels must be whole numbers.",
                          required=False, default=False),
        ]

    def example(self) -> dict:
        return {
            "name": "Relief supply production",
            "activities": ["water_purification", "field_rations", "shelter_kits"],
            "resources": ["labour_hours", "transport_tonnes", "budget_kusd"],
            "value": {"water_purification": 45, "field_rations": 30, "shelter_kits": 60},
            "capacity": {"labour_hours": 900, "transport_tonnes": 240, "budget_kusd": 500},
            "usage": {
                "water_purification|labour_hours": 6, "water_purification|transport_tonnes": 2,
                "water_purification|budget_kusd": 5,
                "field_rations|labour_hours": 3, "field_rations|transport_tonnes": 3,
                "field_rations|budget_kusd": 2,
                "shelter_kits|labour_hours": 9, "shelter_kits|transport_tonnes": 4,
                "shelter_kits|budget_kusd": 8,
            },
            "max_level": {"water_purification": 80, "field_rations": 120, "shelter_kits": 50},
            "min_level": {"field_rations": 20},
            "integral": False,
        }

    def build(self, data: dict) -> ProblemSpec:
        activities = [str(a) for a in self.require(data, "activities")]
        resources = [str(r) for r in self.require(data, "resources")]
        integral = bool(data.get("integral", False))

        parameters = [
            self.indexed("value", ["Activities"], self.require(data, "value"),
                         description="Value produced per unit of activity", default=0.0),
            self.indexed("capacity", ["Resources"], self.require(data, "capacity"),
                         description="Available quantity of each resource"),
            self.indexed("usage", ["Activities", "Resources"], self.require(data, "usage"),
                         description="Resource consumed per unit of activity", default=0.0),
            self.indexed("max_level", ["Activities"], data.get("max_level") or {},
                         description="Upper limit on each activity", default=1e7),
            self.indexed("min_level", ["Activities"], data.get("min_level") or {},
                         description="Committed minimum for each activity", default=0.0),
        ]

        constraints = [
            ProblemConstraint(
                name="respect_capacity",
                statement="Total consumption of each resource cannot exceed what is available.",
                category="physical",
                rationale="Resources are finite; exceeding them is not a plan but a wish.",
                forall=over(r="Resources"),
                rel=le(
                    total(mul(p("usage", i("a"), i("r")), v("level", i("a"))), a="Activities"),
                    p("capacity", i("r")),
                ),
            ),
            ProblemConstraint(
                name="activity_ceiling",
                statement="No activity may exceed its maximum feasible level.",
                category="operational",
                forall=over(a="Activities"),
                rel=le(v("level", i("a")), p("max_level", i("a"))),
            ),
            ProblemConstraint(
                name="activity_floor",
                statement="Activities with existing commitments must meet them.",
                category="policy",
                forall=over(a="Activities"),
                rel=ge(v("level", i("a")), p("min_level", i("a"))),
            ),
        ]

        return ProblemSpec(
            key=data.get("key", "resource_allocation"),
            name=data.get("name", "Resource allocation"),
            problem_type="resource_allocation",
            description=data.get("description", self.summary),
            template_key=self.key,
            template_inputs=data,
            sets=[
                ProblemSet(name="Activities", elements=activities,
                           description="Candidate activities", entity_type="activity"),
                ProblemSet(name="Resources", elements=resources,
                           description="Constrained resources", entity_type="resource"),
            ],
            parameters=parameters,
            variables=[
                ProblemVariable(
                    name="level", index_sets=["Activities"],
                    kind="integer" if integral else "continuous", lb=0.0,
                    ub=float(data.get("level_ub", 1e7)),
                    description="Level at which each activity is run",
                    decision_meaning="How much of this activity to carry out",
                )
            ],
            constraints=constraints,
            objectives=[
                ProblemObjective(
                    name="total_value", statement="Maximise the total value delivered.",
                    sense="maximize",
                    expr=total(mul(p("value", i("a")), v("level", i("a"))), a="Activities"),
                    unit="value units",
                )
            ],
            assumptions=[
                Assumption(
                    key="linear_returns",
                    statement="Value and resource use scale linearly with activity level.",
                    rationale="No economies of scale or saturation effects are modelled.",
                    affects=["total_value", "respect_capacity"],
                ),
                Assumption(
                    key="single_period",
                    statement="All resources and activities belong to one planning period.",
                    rationale="Nothing carries over; timing is out of scope for this template.",
                    affects=["respect_capacity"],
                ),
                Assumption(
                    key="divisible" if not integral else "indivisible",
                    statement=(
                        "Activity levels may take fractional values."
                        if not integral
                        else "Activity levels must be whole units."
                    ),
                    affects=["level"],
                ),
            ],
            scenarios=[
                Scenario(
                    key="austerity", name="Capacity cut by 20%",
                    description="Every resource is 20% scarcer than planned.",
                    overrides=[
                        ScenarioOverride(parameter="capacity", index=[r], scale=0.8)
                        for r in resources
                    ],
                ),
                Scenario(
                    key="surge", name="Capacity increased by 25%",
                    description="Additional resources are released to the operation.",
                    overrides=[
                        ScenarioOverride(parameter="capacity", index=[r], scale=1.25)
                        for r in resources
                    ],
                ),
            ],
        )
