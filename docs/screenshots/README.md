# The pipeline, end to end

Seven screens, in the order the navigation puts them, captured from one real run
against a fresh database. The problem is `examples/commitment_planning.psp` — the
hierarchical commitment plan, written entirely in the problem language.

They are here because a claim about a platform is cheap and a screen of it doing
the thing is not. Every number below appears in the images; none of them was
typed into a mock.

### 1. Domain — [`1-domain.png`](1-domain.png)

The entity types this organisation reasons about, with `unit` selected: all seven
nodes of the tree, each carrying its level, its parent, and how many leaf units
sit beneath it. That last number is the shape `unit_overlap` is built from.

### 2. Author — [`2-author.png`](2-author.png)

The model as text, checked. It compiles to 1,362 columns, 472 rows and 23,198
non-zeros, fingerprint `be6ea9a406fc`. The rules are read back in English, so the
screen states what the model means without anyone reading the algebra.

### 3. Problem — [`3-problem.png`](3-problem.png)

The saved problem: decisions, constraints with their categories and reasons,
index sets, and the assumptions the answer will rest on — each with the reason it
is believed. The parameter table names the source of every value.

### 4. Model — [`4-model.png`](4-model.png)

Compile only; nothing is solved here. The fingerprint matches the Author screen,
which is the point of having one. Engine eligibility is decided from the model's
capabilities before any engine is loaded: CP-SAT needs a fully discrete model,
NetworkX cannot take binary variables, HiGHS can. Each generated row keeps the
statement of the family it came from.

### 5. Scenario — [`5-scenario.png`](5-scenario.png)

The same problem under four sets of assumptions, solved identically: surge 410,
baseline 495, invest 555, serialised 465. `assessment_stood_down` comes back at
495 with 86 decisions — standing one leaf unit down for a week costs no coverage
and moves the work instead, which is availability travelling up the tree.

Shadow prices refuse rather than mislead: the model has integer variables, so no
dual exists, and the screen says which method does work.

### 6. Solve — [`6-solve.png`](6-solve.png)

Caught mid-run. The interface hands over to Results the moment a solve finishes,
so this is the streamed stage log still in flight — compile, model size, and the
flat model going out to a separate solver process.

### 7. Results — [`7-results.png`](7-results.png)

A decision booked at a *branch*, `allocate[joint_exercise, operations, W2 Wed AM]`,
explained. The explanation is derived from the model rather than generated: the
binding constraints, the rule that removed 120 alternatives, and the assumptions
the decision rests on. The provenance chain is 302 nodes and 517 edges, from the
source through facts, parameters, constraints, the model, the run and the
solution to each decision.

## Reproducing them

```
cd backend && PYTHONPATH=. ../.venv/bin/uvicorn psp.main:app
cd frontend && npx vite
```

Then walk the navigation left to right, pasting `examples/commitment_planning.psp`
into the Author screen.
