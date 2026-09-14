"""Transportation: move goods from supply points to demand points at least cost.

This template also declares a ``min_cost_flow`` structure hint, which lets the
platform hand the very same problem to a specialised network algorithm instead
of a general MIP engine — without the analyst changing anything.
"""

from __future__ import annotations

from psp.ir.dsl import cmp, ge, i, le, mul, num, over, p, total, v
from psp.problem.spec import (
    Assumption,
    ProblemConstraint,
    ProblemObjective,
    ProblemSet,
    ProblemSpec,
    ProblemVariable,
    Scenario,
    ScenarioOverride,
    StructureHint,
    Uncertainty,
)
from psp.problem.templates.base import ProblemTemplate, TemplateInput


class TransportationTemplate(ProblemTemplate):
    key = "transportation"
    title = "Transportation"
    summary = (
        "Ship a commodity from sources to destinations at least total cost, "
        "respecting what each source can supply and what each destination needs."
    )
    category = "logistics"
    tags = ["lp", "network", "flow"]

    def inputs(self) -> list[TemplateInput]:
        return [
            TemplateInput(key="sources", label="Sources", kind="entities",
                          description="Where the commodity comes from."),
            TemplateInput(key="destinations", label="Destinations", kind="entities",
                          description="Where the commodity is needed."),
            TemplateInput(key="supply", label="Supply", kind="table",
                          description="Quantity available at each source.",
                          columns=["source", "supply"]),
            TemplateInput(key="demand", label="Demand", kind="table",
                          description="Quantity required at each destination.",
                          columns=["destination", "demand"]),
            TemplateInput(key="cost", label="Unit shipping cost", kind="table",
                          description="Cost of moving one unit along each lane.",
                          columns=["source", "destination", "cost"]),
            TemplateInput(key="lane_capacity", label="Lane capacity", kind="table", required=False,
                          description="Maximum quantity per lane.",
                          columns=["source", "destination", "capacity"]),
        ]

    def example(self) -> dict:
        return {
            "name": "Fuel distribution",
            "sources": ["depot_north", "depot_south", "port_terminal"],
            "destinations": ["base_1", "base_2", "base_3", "forward_post"],
            "supply": {"depot_north": 300, "depot_south": 260, "port_terminal": 400},
            "demand": {"base_1": 220, "base_2": 180, "base_3": 300, "forward_post": 120},
            "cost": {
                "depot_north|base_1": 4, "depot_north|base_2": 6,
                "depot_north|base_3": 9, "depot_north|forward_post": 12,
                "depot_south|base_1": 7, "depot_south|base_2": 3,
                "depot_south|base_3": 5, "depot_south|forward_post": 8,
                "port_terminal|base_1": 6, "port_terminal|base_2": 8,
                "port_terminal|base_3": 4, "port_terminal|forward_post": 5,
            },
            "lane_capacity": {"depot_north|forward_post": 40, "port_terminal|base_3": 250},
        }

    def build(self, data: dict) -> ProblemSpec:
        sources = [str(s) for s in self.require(data, "sources")]
        destinations = [str(d) for d in self.require(data, "destinations")]
        lane_capacity = data.get("lane_capacity") or {}

        parameters = [
            self.indexed("supply", ["Sources"], self.require(data, "supply"),
                         description="Quantity available at each source", unit="units"),
            self.indexed("demand", ["Destinations"], self.require(data, "demand"),
                         description="Quantity required at each destination", unit="units"),
            self.indexed("cost", ["Sources", "Destinations"], self.require(data, "cost"),
                         description="Cost per unit shipped on a lane", unit="cost/unit"),
        ]
        if lane_capacity:
            parameters.append(
                self.indexed(
                    "lane_capacity", ["Sources", "Destinations"], lane_capacity,
                    description="Maximum quantity on a lane", default=1e9, unit="units",
                )
            )

        constraints = [
            ProblemConstraint(
                name="supply_limit",
                statement="A source cannot ship more than it holds.",
                category="physical",
                forall=over(s="Sources"),
                rel=le(
                    total(v("ship", i("s"), i("d")), d="Destinations"),
                    p("supply", i("s")),
                ),
            ),
            ProblemConstraint(
                name="meet_demand",
                statement="Every destination receives at least the quantity it requires.",
                category="operational",
                rationale="Demand here is a requirement, not a preference.",
                forall=over(d="Destinations"),
                rel=ge(
                    total(v("ship", i("s"), i("d")), s="Sources"),
                    p("demand", i("d")),
                ),
            ),
        ]
        # A capacity row whose bound is the "no limit" sentinel constrains nothing
        # and still costs a row in every solve, so the family is only emitted when
        # some lane actually has a cap.
        if lane_capacity:
            constraints.append(
                ProblemConstraint(
                    name="lane_limit",
                    statement="No lane carries more than its capacity.",
                    category="physical",
                    forall=over(s="Sources", d="Destinations"),
                    where=cmp("lt", p("lane_capacity", i("s"), i("d")), num(1e9)),
                    rel=le(v("ship", i("s"), i("d")), p("lane_capacity", i("s"), i("d"))),
                )
            )

        return ProblemSpec(
            key=data.get("key", "transportation"),
            name=data.get("name", "Transportation"),
            problem_type="transportation",
            description=data.get("description", self.summary),
            template_key=self.key,
            template_inputs=data,
            sets=[
                ProblemSet(name="Sources", elements=sources, entity_type="location",
                           description="Supply points"),
                ProblemSet(name="Destinations", elements=destinations, entity_type="location",
                           description="Demand points"),
            ],
            parameters=parameters,
            variables=[
                ProblemVariable(
                    name="ship", index_sets=["Sources", "Destinations"], kind="continuous",
                    lb=0.0, description="Quantity shipped on each lane",
                    decision_meaning="How much to move from this source to this destination",
                )
            ],
            constraints=constraints,
            objectives=[
                ProblemObjective(
                    name="total_cost", statement="Minimise total shipping cost.",
                    sense="minimize",
                    expr=total(
                        mul(p("cost", i("s"), i("d")), v("ship", i("s"), i("d"))),
                        s="Sources", d="Destinations",
                    ),
                    unit="cost units",
                )
            ],
            # Only claim network structure when no lane cap is in play: a capacitated
            # lane is still a flow problem, but the uncapacitated case is the one this
            # template can hand to network simplex without further checks.
            structure=(
                StructureHint(
                    kind="min_cost_flow",
                    arc_variable="ship",
                    cost_parameter="cost",
                    supply_parameter="supply",
                    demand_parameter="demand",
                    tail_set="Sources",
                    head_set="Destinations",
                )
                if not lane_capacity
                else None
            ),
            assumptions=[
                Assumption(
                    key="single_commodity",
                    statement="All shipped units are interchangeable.",
                    rationale="Product mix, priority and perishability are out of scope.",
                    affects=["ship"],
                ),
                Assumption(
                    key="linear_cost",
                    statement=(
                        "Shipping cost is proportional to quantity, with no fixed cost per lane."
                    ),
                    rationale=(
                        "Opening a lane is assumed free; use a facility-location model if not."
                    ),
                    affects=["total_cost"],
                ),
                Assumption(
                    key="deterministic_demand",
                    statement="Supply and demand are known exactly at planning time.",
                    affects=["supply", "demand"],
                ),
            ],
            scenarios=[
                Scenario(
                    key="demand_surge", name="Demand up 30%",
                    description="All destinations require 30% more than planned.",
                    overrides=[
                        ScenarioOverride(parameter="demand", index=[d], scale=1.3)
                        for d in destinations
                    ],
                ),
                Scenario(
                    key="source_lost", name="Largest source offline",
                    description="The largest source becomes unavailable.",
                    overrides=[
                        ScenarioOverride(
                            parameter="supply",
                            index=[max(sources, key=lambda s: float(data["supply"].get(s, 0)))],
                            value=0.0,
                        )
                    ],
                ),
            ],
            uncertainty=[
                Uncertainty(parameter="demand", index=[d], distribution="uniform",
                            low=0.85, high=1.25)
                for d in destinations
            ],
        )
