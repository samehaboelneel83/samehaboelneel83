# The pipeline, end to end

Seven screens, in the order the navigation puts them, captured from one real run
against a fresh database. The problem is `examples/commitment_planning.psp` — the
hierarchical commitment plan, written entirely in the problem language.

They are here because a claim about a platform is cheap and a screen of it doing
the thing is not. Every number below appears in the images; none of them was
typed into a mock.

They were re-taken when the model's balance objective changed from a range to a
variance, because screenshots that no longer show the model are worse than no
screenshots at all.

### 1. Domain — [`1-domain.png`](1-domain.png)

The entity types this organisation reasons about, with `unit` selected: all seven
nodes of the tree, each carrying its level, its parent, and how many leaf units
sit beneath it. That last number is the shape `unit_overlap` is built from.

### 2. Author — [`2-author.png`](2-author.png)

The model as text, checked. It compiles to 1,360 columns, 464 rows and 23,182
non-zeros, fingerprint `c92f69f4de0d`. The rules are read back in English, so the
screen states what the model means without anyone reading the algebra.

### 3. Problem — [`3-problem.png`](3-problem.png)

The saved problem: decisions, constraints with their categories and reasons,
index sets, and the assumptions the answer will rest on — each with the reason it
is believed. The parameter table names the source of every value.

### 4. Model — [`4-model.png`](4-model.png)

Compile only; nothing is solved here. The fingerprint matches the Author screen,
which is the point of having one.

The kind reads **Discrete, quadratic**, and that one word decides the engine.
Balance in this model is a variance, so the objective carries degree-two terms,
and eligibility follows from capability rather than preference: CP-SAT takes it,
HiGHS is refused because it has no mixed-integer quadratic mode, NetworkX
because it takes a linear objective only. The same model with continuous
variables would go the other way, to HiGHS. Each generated row keeps the
statement of the family it came from.

### 5. Scenario — [`5-scenario.png`](5-scenario.png)

The same problem under four sets of assumptions, solved identically: surge 410,
baseline 495, invest 555, serialised 465. `assessment_stood_down` comes back at
495 — standing one leaf unit down for a week costs no coverage and moves the
work instead, which is availability travelling up the tree.

Those five figures are the ones this model produced when balance was a range
rather than a variance, and they are unchanged. That is the evidence that
retuning the objective weights worked: a squared balance term sits on a scale
thousands of times larger than the range it replaced, and weights left alone
would have let it start dropping commitments to tidy the distribution. A test
asserts the inequality the weights were derived from.

Shadow prices refuse rather than mislead: the model has integer variables, so no
dual exists, and the screen says which method does work.

### 6. Solve — [`6-solve.png`](6-solve.png)

Caught mid-run. The interface hands over to Results the moment a solve finishes,
so this is the streamed stage log still in flight — compile, model size, and the
flat model going out to a separate solver process.

### 7. Results — [`7-results.png`](7-results.png)

A decision booked at a *branch* explained. The explanation is derived from the
model rather than generated: the binding constraints, the rules that removed the
alternatives, and the assumptions the decision rests on — including that balance
here means a variance about the mean. The provenance chain runs from the source
through facts, parameters, constraints, the model, the run and the solution to
each of the 91 decisions.

## Reproducing them

```
cd backend && PYTHONPATH=. ../.venv/bin/uvicorn psp.main:app
cd frontend && npx vite
```

Then walk the navigation left to right, pasting `examples/commitment_planning.psp`
into the Author screen.
