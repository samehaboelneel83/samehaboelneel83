"""End-to-end behaviour through the HTTP API."""

from __future__ import annotations


def test_health_admits_when_authentication_is_off(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["authentication"] == "disabled"
    # An open API must say so rather than reporting a bare "ok".
    assert any("authentication is disabled" in w for w in body["warnings"])


def test_solver_registry_is_reported_without_loading_an_engine(client):
    body = client.get("/api/solvers").json()
    names = {s["name"] for s in body["solvers"]}
    assert names == {"highs", "cpsat", "networkx"}
    assert "isolated worker process" in body["execution"]

    cpsat = next(s for s in body["solvers"] if s["name"] == "cpsat")
    assert cpsat["capabilities"]["continuous"] is False
    assert cpsat["capabilities"]["requires_bounded_integers"] is True


def test_templates_are_listed_with_their_inputs(client):
    body = client.get("/api/templates").json()
    keys = {t["key"] for t in body["templates"]}
    assert keys == {
        "resource_allocation", "assignment", "scheduling",
        "transportation", "vehicle_routing", "lecture_timetabling",
    }
    for template in body["templates"]:
        assert template["inputs"], f"{template['key']} documents no inputs"


def test_full_lifecycle_instantiate_compile_solve_explain(client):
    example = client.get("/api/templates/transportation/example").json()["data"]
    example["key"] = "lifecycle-transport"
    example.pop("lane_capacity")

    created = client.post(
        "/api/problems", json={"template": "transportation", "data": example}
    )
    assert created.status_code == 201, created.text
    key = created.json()["key"]

    spec = created.json()["spec"]
    assert spec["assumptions"], "a problem without recorded assumptions is not defensible"
    assert all(c["statement"] for c in spec["constraints"])

    compiled = client.post(f"/api/problems/{key}/compile").json()
    assert compiled["statistics"]["variables"] > 0
    assert compiled["structure"] == "min_cost_flow"
    assert compiled["record"]["mappings"], "compilation recorded no problem-to-IR mapping"

    solved = client.post(f"/api/problems/{key}/solve", json={}).json()
    assert solved["solution"]["status"] == "optimal"
    assert solved["solution"]["objectives"][0]["sense"] == "minimize"
    assert solved["solution"]["decisions"]
    # Specialised structure was recognised, so the network engine should win.
    assert solved["selection"]["solver"] == "networkx"

    decision = solved["solution"]["decisions"][0]["key"]
    explained = client.get(
        f"/api/solutions/{solved['solution_id']}/explain", params={"decision": decision}
    )
    assert explained.status_code == 200, explained.text
    body = explained.json()
    assert body["narrative"]
    assert body["decision"] == decision

    provenance = client.get(f"/api/solutions/{solved['solution_id']}/provenance").json()
    kinds = {n["kind"] for n in provenance["nodes"]}
    # The whole chain must be present, end to end.
    assert {"source", "fact", "parameter", "constraint", "model", "run", "solution",
            "decision"} <= kinds
    assert provenance["edges"]


def test_compile_reports_the_offending_constraint_not_a_stack_trace(client):
    example = client.get("/api/templates/assignment/example").json()["data"]
    example["key"] = "broken-assignment"
    created = client.post("/api/problems", json={"template": "assignment", "data": example})
    key = created.json()["key"]

    spec = created.json()["spec"]
    # Make a constraint multiply two decision variables.
    spec["constraints"][0]["rel"] = {
        "op": "le",
        "lhs": {"op": "mul", "args": [
            {"op": "var", "name": "assign", "index": [
                {"op": "lit", "value": "crew_alpha"}, {"op": "lit", "value": "flood_zone_a"}]},
            {"op": "var", "name": "assign", "index": [
                {"op": "lit", "value": "crew_bravo"}, {"op": "lit", "value": "flood_zone_b"}]},
        ]},
        "rhs": {"op": "const", "value": 1.0},
    }
    client.put(f"/api/problems/{key}", json={"spec": spec})

    response = client.post(f"/api/problems/{key}/compile")
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "decision variables" in detail["error"]
    assert detail["where"] and "constraint" in detail["where"]


def test_requesting_an_incapable_solver_is_an_error_not_a_substitution(client):
    example = client.get("/api/templates/transportation/example").json()["data"]
    example["key"] = "explicit-solver"
    client.post("/api/problems", json={"template": "transportation", "data": example})

    response = client.post(
        "/api/problems/explicit-solver/solve", json={"solver": "cpsat"}
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "cpsat" in detail["error"]
    assert any(c["solver"] == "highs" and c["eligible"] for c in detail["considered"])


def test_scenario_comparison_lines_answers_up_against_the_baseline(client):
    example = client.get("/api/templates/resource_allocation/example").json()["data"]
    example["key"] = "compare-allocation"
    client.post("/api/problems", json={"template": "resource_allocation", "data": example})

    body = client.post(
        "/api/problems/compare-allocation/compare",
        json={"scenarios": [None, "austerity", "surge"]},
    ).json()
    rows = {r["scenario"]: r for r in body["comparison"]}
    assert set(rows) == {"baseline", "austerity", "surge"}
    assert rows["austerity"]["delta_vs_baseline"] < 0 < rows["surge"]["delta_vs_baseline"]


def test_unknown_scenario_is_rejected(client):
    example = client.get("/api/templates/resource_allocation/example").json()["data"]
    example["key"] = "scenario-check"
    client.post("/api/problems", json={"template": "resource_allocation", "data": example})
    response = client.post(
        "/api/problems/scenario-check/compare", json={"scenarios": ["does-not-exist"]}
    )
    assert response.status_code == 404


def test_sensitivity_ranks_the_parameters_that_matter(client):
    example = client.get("/api/templates/resource_allocation/example").json()["data"]
    example["key"] = "sensitivity-allocation"
    client.post("/api/problems", json={"template": "resource_allocation", "data": example})

    body = client.post(
        "/api/problems/sensitivity-allocation/sensitivity",
        json={"method": "one_at_a_time", "parameters": ["capacity", "value"],
              "multipliers": [0.9, 1.1], "max_solves": 8},
    ).json()
    assert body["baseline_objective"]
    assert body["ranking"]
    assert {r["parameter"] for r in body["ranking"]} <= {"capacity", "value"}


def test_shadow_price_method_declines_on_an_integer_model(client):
    example = client.get("/api/templates/assignment/example").json()["data"]
    example["key"] = "sensitivity-assignment"
    client.post("/api/problems", json={"template": "assignment", "data": example})

    body = client.post(
        "/api/problems/sensitivity-assignment/sensitivity",
        json={"method": "shadow_prices"},
    ).json()
    # Quoting a dual for an integer model would be a made-up number.
    assert body["ranking"] == []
    assert any("integer variables" in note for note in body["notes"])


def test_explanation_refuses_to_reinterpret_an_edited_problem(client):
    example = client.get("/api/templates/resource_allocation/example").json()["data"]
    example["key"] = "drifting-problem"
    created = client.post(
        "/api/problems", json={"template": "resource_allocation", "data": example}
    )
    solved = client.post("/api/problems/drifting-problem/solve", json={}).json()
    decision = solved["solution"]["decisions"][0]["key"]

    spec = created.json()["spec"]
    spec["parameters"][0]["values"][0]["value"] = 999.0
    client.put("/api/problems/drifting-problem", json={"spec": spec})

    response = client.get(
        f"/api/solutions/{solved['solution_id']}/explain", params={"decision": decision}
    )
    assert response.status_code == 409
    assert "changed since this run" in response.json()["detail"]


def test_runs_and_audit_trail_record_what_happened(client):
    example = client.get("/api/templates/assignment/example").json()["data"]
    example["key"] = "audited-assignment"
    client.post("/api/problems", json={"template": "assignment", "data": example})
    client.post("/api/problems/audited-assignment/solve", json={})

    runs = client.get("/api/runs", params={"problem": "audited-assignment"}).json()["runs"]
    assert runs and runs[0]["status"] == "optimal"

    detail = client.get(f"/api/runs/{runs[0]['id']}").json()
    assert detail["model_version"]["fingerprint"]
    assert detail["selection_trace"]["considered"]

    actions = {e["action"] for e in client.get("/api/audit").json()["events"]}
    assert {"problem.saved", "run.completed"} <= actions


def test_domain_entities_project_into_an_index_set(client):
    client.post("/api/domain/entity-types", json={"key": "depot", "name": "Depot"})
    for key, name in (("north", "Northern depot"), ("south", "Southern depot")):
        client.post("/api/domain/entities", json={
            "entity_type": "depot", "key": key, "name": name,
            "attributes": {"capacity": 100}, "latitude": 51.5, "longitude": -0.1,
        })

    body = client.get("/api/domain/entities/depot/set").json()
    assert body["elements"] == ["north", "south"]
    assert body["attributes"]["north"]["capacity"] == 100


def test_duplicate_entity_is_rejected(client):
    client.post("/api/domain/entity-types", json={"key": "vehicle", "name": "Vehicle"})
    first = client.post("/api/domain/entities", json={
        "entity_type": "vehicle", "key": "truck-1", "name": "Truck 1"})
    assert first.status_code == 201
    again = client.post("/api/domain/entities", json={
        "entity_type": "vehicle", "key": "truck-1", "name": "Truck 1"})
    assert again.status_code == 409


def test_simulation_sweep_shows_where_the_queue_becomes_unstable(client):
    body = client.post("/api/simulate/queue/sweep", json={
        "base": {
            "servers": 2, "horizon_hours": 8, "replications": 8, "seed": 3,
            "streams": [
                {"name": "urgent", "rate_per_hour": 4, "service_minutes_mean": 25,
                 "service_minutes_stddev": 8, "priority": 0},
                {"name": "routine", "rate_per_hour": 6, "service_minutes_mean": 18,
                 "service_minutes_stddev": 6, "priority": 2},
            ],
        },
        "server_counts": [2, 3, 5],
    }).json()
    rows = {r["servers"]: r for r in body["sweep"]}
    assert rows[2]["stable"] is False
    assert rows[5]["stable"] is True
    assert rows[5]["mean_wait_minutes"] < rows[2]["mean_wait_minutes"]


def test_websocket_streams_the_stages_of_a_solve(client):
    example = client.get("/api/templates/assignment/example").json()["data"]
    example["key"] = "ws-assignment"
    client.post("/api/problems", json={"template": "assignment", "data": example})

    with client.websocket_connect("/ws/solve") as ws:
        ws.send_json({"problem": "ws-assignment"})
        stages = []
        while True:
            message = ws.receive_json()
            stages.append(message["stage"])
            if message["stage"] in ("complete", "error"):
                final = message
                break
    assert stages == ["compiling", "compiled", "solving", "solved", "complete"]
    assert final["solution"]["status"] == "optimal"


def test_template_rejects_impossible_data_with_422(client):
    example = client.get("/api/templates/vehicle_routing/example").json()["data"]
    example["vehicle_capacity"] = 1
    response = client.post("/api/problems", json={"template": "vehicle_routing", "data": example})
    assert response.status_code == 422
    assert "capacity" in response.json()["detail"]


def test_a_problem_can_be_written_checked_saved_and_solved(client):
    source = """
problem crew_cover "Crew coverage"
  "Cover every shift with the fewest crews."

set Crews = alpha, bravo, charlie
set Shifts = morning, evening, night

param cost[Crews] = { alpha: 3, bravo: 2, charlie: 4 }
param qualified[Crews, Shifts] default 1 = { (charlie, night): 0 }
param needed[Shifts] default 1

var assign[Crews, Shifts] binary means "Put this crew on this shift"

constraint cover "Every shift gets the crews it needs"
  forall s in Shifts:
    sum(assign[c, s] for c in Crews) >= needed[s]

constraint one_shift "No crew works two shifts"
  category physical
  forall c in Crews:
    sum(assign[c, s] for s in Shifts) <= 1

constraint only_qualified "A crew is only put on shifts it is qualified for"
  category regulatory
  forall c in Crews, s in Shifts:
    assign[c, s] <= qualified[c, s]

minimize crew_cost "Use the cheapest crews" unit "cost units":
  sum(cost[c] * assign[c, s] for c in Crews, s in Shifts)

assume crews_interchangeable "Any qualified crew covers a shift equally well"
"""
    checked = client.post("/api/dsl/check", json={"source": source})
    assert checked.status_code == 200, checked.text
    body = checked.json()
    assert body["key"] == "crew_cover"
    assert body["statistics"]["variables"] == 9
    assert [c["name"] for c in body["summary"]["constraints"]] == [
        "cover", "one_shift", "only_qualified",
    ]

    created = client.post("/api/dsl/problems", json={"source": source})
    assert created.status_code == 201, created.text

    solved = client.post("/api/problems/crew_cover/solve", json={}).json()
    assert solved["solution"]["status"] == "optimal"
    # Three shifts, one crew each, and charlie cannot take the night.
    assert len(solved["solution"]["decisions"]) == 3
    placed = {tuple(d["index"]) for d in solved["solution"]["decisions"]}
    assert ("charlie", "night") not in placed


def test_a_language_error_comes_back_located(client):
    response = client.post("/api/dsl/check", json={
        "source": 'problem p\nset A = a\nvar v[A] binary\nconstraint c "s"\n'
                  '  forall i in A: v[i] <= suply[i]\n',
    })
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["kind"] == "dsl"
    assert detail["line"] == 5
    assert "not a declared parameter" in detail["message"]
    # Without the excerpt the author is hunting; with it the caret is on the word.
    assert "suply" in detail["excerpt"]


def test_every_template_can_be_read_as_source(client):
    listed = client.get("/api/templates").json()["templates"]
    for template in listed:
        body = client.get(f"/api/dsl/templates/{template['key']}").json()
        assert body["source"].startswith("problem ")
        # What comes back must itself be valid input.
        assert client.post("/api/dsl/check", json={"source": body["source"]}).status_code == 200


def test_a_stored_problem_can_be_read_back_as_source(client):
    example = client.get("/api/templates/assignment/example").json()["data"]
    example["key"] = "export-me"
    client.post("/api/problems", json={"template": "assignment", "data": example})

    body = client.get("/api/dsl/problems/export-me").json()
    # The key contains a hyphen, so the writer quotes it — bare export-me would
    # read as a subtraction — and the parser has to accept it back.
    assert 'problem "export-me"' in body["source"]
    assert client.post("/api/dsl/check", json={"source": body["source"]}).status_code == 200
