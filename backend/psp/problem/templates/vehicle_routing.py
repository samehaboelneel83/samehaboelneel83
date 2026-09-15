"""Capacitated vehicle routing.

Formulated as an arc-selection model with Miller-Tucker-Zemlin subtour
elimination. MTZ is chosen deliberately: it is compact and stays inside the
linear IR, so no solver-specific callback or lazy-constraint machinery leaks
into the platform. It is weaker than a cutting-plane formulation and will not
scale to hundreds of stops — which the template says out loud, as an
assumption, rather than letting an operator discover it in production.
"""

from __future__ import annotations

from psp.ir.dsl import (
    all_of, differ, eq, exactly, ge, i, is_not, le, mul, num, over, p, sub, total, v,
)
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

MAX_PRACTICAL_STOPS = 25


class VehicleRoutingTemplate(ProblemTemplate):
    key = "vehicle_routing"
    title = "Vehicle routing"
    summary = (
        "Build vehicle routes out of a depot that serve every stop exactly once, "
        "respect vehicle capacity, and minimise total distance."
    )
    category = "logistics"
    tags = ["mip", "routing", "mtz"]

    def inputs(self) -> list[TemplateInput]:
        return [
            TemplateInput(key="depot", label="Depot", kind="entities",
                          description="The location every route starts and ends at."),
            TemplateInput(key="stops", label="Stops", kind="entities",
                          description="Locations that must be visited."),
            TemplateInput(key="distance", label="Distance", kind="table",
                          description="Travel distance or time between every pair of locations.",
                          columns=["from", "to", "distance"]),
            TemplateInput(key="demand", label="Stop demand", kind="table",
                          description="Load picked up or dropped at each stop.",
                          columns=["stop", "demand"]),
            TemplateInput(key="vehicle_capacity", label="Vehicle capacity", kind="number",
                          description="Load one vehicle can carry."),
            TemplateInput(key="vehicles", label="Number of vehicles", kind="number",
                          description="How many vehicles leave the depot."),
        ]

    def example(self) -> dict:
        coords = {
            "depot": (0, 0), "clinic_a": (2, 3), "clinic_b": (5, 1),
            "shelter_c": (1, 6), "shelter_d": (6, 5), "post_e": (4, 8),
        }
        distance = {}
        for a, (ax, ay) in coords.items():
            for b, (bx, by) in coords.items():
                if a != b:
                    distance[f"{a}|{b}"] = round(((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5, 2)
        return {
            "name": "Medical resupply round",
            "depot": "depot",
            "stops": ["clinic_a", "clinic_b", "shelter_c", "shelter_d", "post_e"],
            "distance": distance,
            "demand": {"clinic_a": 4, "clinic_b": 6, "shelter_c": 5, "shelter_d": 7, "post_e": 3},
            "vehicle_capacity": 15,
            "vehicles": 2,
        }

    def build(self, data: dict) -> ProblemSpec:
        depot = str(self.require(data, "depot"))
        stops = [str(s) for s in self.require(data, "stops")]
        capacity = float(self.require(data, "vehicle_capacity"))
        vehicles = int(self.require(data, "vehicles"))
        demand = {str(k): float(v_) for k, v_ in self.require(data, "demand").items()}

        if depot in stops:
            raise ValueError(f"the depot '{depot}' must not also appear in the stop list")
        nodes = [depot, *stops]
        if len(nodes) > MAX_PRACTICAL_STOPS:
            raise ValueError(
                f"this template uses an MTZ formulation and is intended for up to "
                f"{MAX_PRACTICAL_STOPS} locations; {len(nodes)} were given"
            )
        total_demand = sum(demand.get(s, 0.0) for s in stops)
        if total_demand > capacity * vehicles:
            raise ValueError(
                f"total demand ({total_demand:g}) exceeds the fleet's capacity "
                f"({vehicles} x {capacity:g} = {capacity * vehicles:g}); no routing can serve it"
            )

        parameters = [
            self.indexed("distance", ["Nodes", "Nodes"], self.require(data, "distance"),
                         description="Travel cost between locations", default=1e6, unit="distance"),
            self.indexed("demand", ["Nodes"], {**demand, depot: 0.0},
                         description="Load required at each location", default=0.0),
            self.indexed("capacity", [], {"": capacity},
                         description="Capacity of one vehicle"),
            self.indexed("vehicles", [], {"": float(vehicles)},
                         description="Number of vehicles dispatched"),
        ]

        constraints = [
            ProblemConstraint(
                name="arrive_once",
                statement="Every stop is entered exactly once.",
                category="operational",
                forall=over(j="Nodes"),
                where=is_not("j", depot),
                rel=exactly(
                    1,
                    v("travel", i("k"), i("j")), where=differ("k", "j"), k="Nodes"
                ),
            ),
            ProblemConstraint(
                name="depart_once",
                statement="Every stop is left exactly once.",
                category="operational",
                forall=over(j="Nodes"),
                where=is_not("j", depot),
                rel=exactly(
                    1,
                    v("travel", i("j"), i("k")), where=differ("k", "j"), k="Nodes"
                ),
            ),
            ProblemConstraint(
                name="fleet_departs",
                statement="Exactly the available vehicles leave the depot.",
                category="operational",
                rationale="Routes are not optional: an idle vehicle is modelled by a short route.",
                rel=eq(
                    total(v("travel", depot, i("k")), where=is_not("k", depot), k="Nodes"),
                    p("vehicles"),
                ),
            ),
            ProblemConstraint(
                name="fleet_returns",
                statement="Every vehicle that leaves the depot returns to it.",
                category="physical",
                rel=eq(
                    total(v("travel", i("k"), depot), where=is_not("k", depot), k="Nodes"),
                    p("vehicles"),
                ),
            ),
            ProblemConstraint(
                name="no_self_loop",
                statement="A vehicle cannot travel from a location to itself.",
                category="modelling",
                forall=over(a="Nodes"),
                rel=eq(v("travel", i("a"), i("a")), num(0)),
            ),
            # MTZ: load[j] >= load[i] + demand[j] - capacity * (1 - travel[i,j]).
            # When the arc is used the inequality forces the load to accumulate along
            # the route, which both eliminates subtours and enforces capacity.
            ProblemConstraint(
                name="load_accumulates",
                statement=(
                    "Load carried after a stop exceeds the load before it by that stop's "
                    "demand, which rules out routes that never return to the depot."
                ),
                category="modelling",
                rationale=(
                    "Miller-Tucker-Zemlin subtour elimination, doubling as capacity tracking."
                ),
                forall=over(a="Nodes", b="Nodes"),
                where=all_of(differ("a", "b"), is_not("a", depot), is_not("b", depot)),
                rel=ge(
                    sub(v("load", i("b")), v("load", i("a"))),
                    sub(
                        p("demand", i("b")),
                        mul(p("capacity"), sub(num(1), v("travel", i("a"), i("b")))),
                    ),
                ),
            ),
            ProblemConstraint(
                name="load_at_least_demand",
                statement="A vehicle leaving a stop carries at least that stop's demand.",
                category="physical",
                forall=over(a="Nodes"),
                where=is_not("a", depot),
                rel=ge(v("load", i("a")), p("demand", i("a"))),
            ),
            ProblemConstraint(
                name="load_within_capacity",
                statement="A vehicle never carries more than its capacity.",
                category="physical",
                forall=over(a="Nodes"),
                where=is_not("a", depot),
                rel=le(v("load", i("a")), p("capacity")),
            ),
        ]

        return ProblemSpec(
            key=data.get("key", "vehicle_routing"),
            name=data.get("name", "Vehicle routing"),
            problem_type="vehicle_routing",
            description=data.get("description", self.summary),
            template_key=self.key,
            template_inputs=data,
            sets=[
                ProblemSet(name="Nodes", elements=nodes, entity_type="location",
                           description="Depot and stops"),
            ],
            parameters=parameters,
            variables=[
                ProblemVariable(
                    name="travel", index_sets=["Nodes", "Nodes"], kind="binary", lb=0.0, ub=1.0,
                    description="Whether a vehicle travels directly between two locations",
                    decision_meaning="Drive this leg",
                ),
                ProblemVariable(
                    name="load", index_sets=["Nodes"], kind="integer",
                    lb=0.0, ub=capacity,
                    description="Cumulative load once the stop has been served",
                    decision_meaning="Load on board after this stop",
                ),
            ],
            constraints=constraints,
            objectives=[
                ProblemObjective(
                    name="total_distance", statement="Minimise total distance travelled.",
                    sense="minimize",
                    expr=total(
                        mul(p("distance", i("a"), i("b")), v("travel", i("a"), i("b"))),
                        where=differ("a", "b"), a="Nodes", b="Nodes",
                    ),
                    unit="distance",
                )
            ],
            assumptions=[
                Assumption(
                    key="homogeneous_fleet",
                    statement="All vehicles are identical and share one capacity.",
                    affects=["capacity", "fleet_departs"],
                ),
                Assumption(
                    key="mtz_scale",
                    statement=(
                        f"The MTZ formulation is used, which is practical to about "
                        f"{MAX_PRACTICAL_STOPS} locations."
                    ),
                    rationale=(
                        "Its linear relaxation is weak; larger instances need a "
                        "cutting-plane or metaheuristic engine."
                    ),
                    affects=["load_accumulates"],
                ),
                Assumption(
                    key="no_time_windows",
                    statement="Stops may be served at any time; only distance matters.",
                    rationale="Time windows, service durations and shift limits are not modelled.",
                    affects=["total_distance"],
                ),
                Assumption(
                    key="integral_load",
                    statement="Loads are whole units, so the model stays fully discrete.",
                    rationale="This keeps CP-SAT eligible alongside the MIP engine.",
                    affects=["load"],
                ),
            ],
            scenarios=[
                Scenario(
                    key="one_vehicle_down", name="One vehicle unavailable",
                    description="The fleet is reduced by one vehicle.",
                    overrides=[ScenarioOverride(parameter="vehicles", index=[],
                                                value=float(max(1, vehicles - 1)))],
                ),
                Scenario(
                    key="heavier_loads", name="Demand up 20%",
                    description="Every stop needs 20% more than planned.",
                    overrides=[
                        ScenarioOverride(parameter="demand", index=[s], scale=1.2) for s in stops
                    ],
                ),
            ],
        )
