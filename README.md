# Problem-Solving Platform

A generic platform for stating operational problems, compiling them
deterministically into a solver-independent model, solving them with whichever
engine is capable, and explaining every recommendation back to the data it came
from — without a language model anywhere in the chain.

```
Problem Model  ──▶  Model IR  ──▶  Flat linear system  ──▶  Solver adapter
     │                                                            │
     └──────────────────── provenance ◀──────────────────────────┘
```

The valuable part of this repository is not that it uses OR-Tools, PostgreSQL
or Keycloak. It is the **Model IR**, the **deterministic compiler** that targets
it, and the **provenance chain** that falls out of compilation as a by-product.
Those three turn a collection of optimisation libraries into a platform.

## Run it

Nothing external is required. SQLite and an open API are the defaults, so a
first run needs only Python and Node.

```bash
# API — http://localhost:8000  (docs at /docs)
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
cd backend && PYTHONPATH=. ../.venv/bin/uvicorn psp.main:app --reload

# UI — http://localhost:5173
cd frontend && npm install && npm run dev
```

The whole stack, with PostgreSQL + PostGIS and Keycloak:

```bash
docker compose up --build      # UI on :8081, API on :8000, Keycloak on :8080
```

Tests and linting:

```bash
cd backend && PYTHONPATH=. ../.venv/bin/python -m pytest && ../.venv/bin/ruff check psp tests
cd frontend && npm run typecheck && npm run build
```

## The five minutes that explain the whole thing

```bash
# 1. State a problem from a template
curl -sX POST localhost:8000/api/problems -H 'Content-Type: application/json' \
  -d "{\"template\":\"transportation\",\"data\":$(curl -s localhost:8000/api/templates/transportation/example | python3 -c 'import json,sys; d=json.load(sys.stdin)["data"]; d.pop("lane_capacity"); d["key"]="fuel"; print(json.dumps(d))')}"

# 2. Compile without solving — see the generated system and which engines fit
curl -sX POST localhost:8000/api/problems/fuel/compile | python3 -m json.tool | head -30

# 3. Solve. Nothing said which engine to use; structure recognition picked one
curl -sX POST localhost:8000/api/problems/fuel/solve -d '{}' -H 'Content-Type: application/json'

# 4. Force a general-purpose engine instead. Same model, same optimum
curl -sX POST localhost:8000/api/problems/fuel/solve -d '{"solver":"highs"}' -H 'Content-Type: application/json'
```

Step 3 solves it with network simplex and step 4 with a MIP engine. The answers
match, and the problem model never changed. That is what the IR is for.

The same walk through the interface, screen by screen, is in
[`docs/screenshots/`](docs/screenshots/README.md).

## Architecture

| Layer | What it holds | Depends on |
|---|---|---|
| **Domain model** | Entity types, entities, hierarchies, geography — what problems are *about* | nothing |
| **Problem model** | Sets, parameters, decisions, constraints, objectives, assumptions, scenarios | domain |
| **Model IR** | Sets, parameters, variables, expressions, constraints, objectives | nothing |
| **Compiler** | Problem → IR → flat linear system, plus the mapping between them | IR |
| **Solvers** | HiGHS, OR-Tools CP-SAT, NetworkX | flat model only |
| **Provenance** | source → fact → parameter → constraint → model → run → solution → decision | all of the above |

The dependency direction is enforced by convention and checked by review:
nothing below the IR imports a solver, and no solver imports the problem or
domain layers. That is what makes replacing an engine a contained change.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the reasoning behind each
decision, and [`docs/DECISIONS.md`](docs/DECISIONS.md) for the ones that were
made deliberately against the obvious choice. [`docs/ROADMAP.md`](docs/ROADMAP.md)
is the other direction: what is missing, and the order it should be built in.

## What is deliberately not here

Neo4j, Kafka, Kubernetes, RDF/OWL, a distributed solver cluster, a dozen engines
and any LLM. None of them is ruled out forever; none of them earns its
operational cost on day one. The architecture leaves room for each: the solver
registry takes new adapters without touching the IR, and the provenance tables
already carry what an OpenLineage exporter would need.

## Stack

React · FastAPI · PostgreSQL (JSONB, ltree, PostGIS) · Keycloak · OR-Tools
CP-SAT · HiGHS · NetworkX · SimPy

## Authoring problems

A problem class can be written as text rather than Python:

```
problem crew_cover "Crew coverage"

set Crews = alpha, bravo, charlie
set Shifts = morning, evening, night
param cost[Crews] = { alpha: 3, bravo: 2, charlie: 4 }
var assign[Crews, Shifts] binary means "Put this crew on this shift"

constraint cover "Every shift gets the crews it needs"
  forall s in Shifts:
    sum(assign[c, s] for c in Crews) >= needed[s]

minimize crew_cost "Use the cheapest crews" unit "cost units":
  sum(cost[c] * assign[c, s] for c in Crews, s in Shifts)

assume crews_interchangeable "Any qualified crew covers a shift equally well"
scenario crew_lost "One crew unavailable"
  set qualified[alpha, morning] to 0
```

It compiles to the same Problem Model the templates produce, so it inherits
assumptions, scenarios, provenance, labels and explanations without asking.
Every built-in template can be read back as source (`GET /api/dsl/templates/{key}`),
which is the fastest way to learn the language — and there is a test asserting
all six survive the round trip with an identical model fingerprint.

### Hierarchies

A tree is declared once, and what the rules need from it is derived:

```
hierarchy Units by parent
  covers unit_covers
  overlap unit_overlap
  leaf is_leaf
  count leaf_count
  = { operations: organisation, training: organisation,
      ops_north: operations, ops_south: operations,
      instruction: training, assessment: training }
```

`unit_covers` is ancestor-or-self, `unit_overlap` the leaves two nodes share,
`is_leaf` and `leaf_count` what they sound like. They are still tables when
they reach the compiler — a rule that counted shared leaves itself would carry
a term for every pair of nodes *and every leaf between them* — but nobody
writes or maintains them. A parent map that is not a tree is refused, by name:
an unknown node, a node as its own parent, a cycle.

`examples/commitment_planning.psp` declares two trees this way and deletes five
tables. Its flat model — columns, rows, right-hand sides, objective — is
byte-identical across all five scenarios to what the hand-written tables
produced, which is the test.

### Soft constraints

A rule is hard unless it says what breaking it costs:

```
constraint honour_days_off "Nobody works a day they asked off"
  category policy
  soft penalty 20
  because "Requests are kept unless keeping them would leave a day uncovered"
  sum(on_duty[p, d] for p in People, d in Days where asked_off[p, d] == 1) == 0
```

The compiler adds the room to break it and charges the objective for using it.
Violations come back as violations — which rule, by how much, in which
direction, at what price — rather than being absorbed into an answer that looks
clean. The penalties appear in the objective breakdown as one component beside
the stated objectives.

Prices are what let a model be handed an impossible week and still return the
plan a sensible person would write. In `examples/duty_roster.psp` a missing
day is priced at 50, an overridden request at 20 and an uneven week at 6; when
one person is away, the plan leaves *Monday* uncovered, because that is the one
uncovered day that also saves a request. Both its answers are checked against
arithmetic done by hand.

### When there is no plan

An infeasible solve comes back with the reason attached, not just the status:

```
No plan satisfies every rule. 1 would have to give.
cover_the_night[Tuesday night]: asks for 0 against a limit of 2, so it is under by 2.
  The rule: Each night is staffed to the level it needs
```

The model is copied, every rule in the copy is allowed to bend, and it is
solved twice — once for the fewest rules that must give, then for the least
they can give by. What comes back is the rules, the quantity against each, and
the parameters behind them with their sources. The real model is never
relaxed: the elastic copy lives for the length of the diagnosis and is thrown
away.

`examples/ward_cover.psp` is the worked example, and exists to be infeasible.
Two nights need two nurses each, one nurse is on leave, and nobody may work
twice: four needed, two available. Its scenarios buy a little help (short by
one instead of two) and then enough (a plan, and nothing to diagnose).

### Quadratic objectives

An objective may multiply two decisions. Nothing else may:

```
minimize imbalance "Spread of team loads about their mean" unit "hours squared":
  sum((load[t] - mean_load) * (load[t] - mean_load) for t in Teams)
```

That is the honest way to say "balanced", and it is why the language has it: a
range — the busiest minus the idlest — is the linear stand-in, and it cannot
tell one overloaded team from three so long as the extremes match.

The compiler folds the product into degree-two terms, decides whether the
result is convex, and the engine follows from that: HiGHS minimises a convex
quadratic over a continuous model, CP-SAT builds a variable per product over a
discrete one. A quadratic over *both* kinds of variable has no engine here and
is refused by name, as is a product of decisions inside a constraint, where a
row has to stay a line a shadow price can be attached to.

`examples/balanced_workload.psp` is the worked example, and its answers are
checked against ones computed by hand.

Swapping a range for a variance is not a free substitution, because the two are
on different scales: a range over these units spans about ten, and its square
spans thousands, so a balance term that was a tiebreaker becomes one that
outbids commitments. `examples/commitment_planning.psp` shows what that costs.
Its objective weights are not tuned by feel — they are derived, in a comment
above them, from the cheapest commitment on one side and the widest the squared
balance term can be on the other, and a test asserts that inequality rather than
the numbers the model happens to produce. Every scenario covers exactly the work
it covered before the measure changed, which is the evidence the derivation
worked.

## Problem templates

`resource_allocation` · `assignment` · `scheduling` · `transportation` ·
`vehicle_routing` · `lecture_timetabling`

Each carries its constraints *and* its assumptions, so an operator who never
opens a solver still sees what the encoding committed them to.
