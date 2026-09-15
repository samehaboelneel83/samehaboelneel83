# Roadmap

Where the platform is against the architecture it is aiming at, what is
genuinely missing, and the order the missing parts should be built in.

The aim is the one the architecture has had from the start: *given entities,
resources, commitments, states, time, constraints, objectives and dependencies,
find the best feasible plan* — with a generic engine underneath, so a faculty
timetable is one template beside workforce allocation, maintenance planning and
capacity planning rather than a module of its own.

This file is a plan, not a record. `DECISIONS.md` holds the choices already
made; anything here can still be argued out of.

---

## Where the platform already is

These are built, tested and load-bearing. Nothing below proposes rebuilding
them.

| Capability | Where it lives |
|---|---|
| **A problem is first class** — variables, constraints, objectives, assumptions and scenarios in one stored object | `psp/problem/spec.py` |
| **The solver is independent of the domain** | `psp/ir/`, `psp/compiler/`, `psp/solvers/registry.py` |
| **Multiple weighted objectives**, each reported in its own direction and unit | `psp/compiler/flatten.py`, `psp/provenance/solution.py` |
| **Scenarios that never mutate the baseline**, and a comparison across them | `psp/problem/spec.py`, `psp/api/routes_runs.py` |
| **Explanation of a decision**, derived from the model rather than generated | `psp/provenance/explain.py` |
| **Sensitivity** — shadow prices where a dual exists, perturb-and-re-solve where it does not | `psp/execution/sensitivity.py` |
| **Problem templates**, six of them, plus a text language so a seventh needs no Python | `psp/problem/templates/`, `psp/dsl/` |
| **Evidence and provenance** — source, fact, parameter, constraint, model, run, solution, decision | `psp/db/models.py`, `psp/provenance/` |

Solver independence is not a claim but a test: the transportation problem
reaches 3240.0 through NetworkX network simplex and through HiGHS, from one
unchanged problem model.

Hierarchy works too, but as a *modelling pattern* rather than a construct —
three simultaneous trees in one model, rollup at every level of each, a
commitment assignable at any level, inheritance downward and blocking upward.
That distinction is the subject of Phase 3.

---

## What is actually missing

Five gaps, established by reading the code rather than by comparing feature
lists.

**Soft constraints do not exist.** Every constraint is hard. Expressing "this
should hold, and here is what it costs when it does not" means hand-rolling a
slack variable and an objective term in every model that wants one.

**Infeasibility is a status, not a diagnosis.** `INFEASIBLE` is mapped from
every engine and returned. Nothing says which constraints conflict, or what
would have to move. A planner told "no feasible plan" learns nothing they can
act on.

**Hierarchy is hand-maintained data.** `unit_covers`, `unit_overlap`,
`is_leaf`, `specialty_covers` and `leaf_count` are all derivable from a parent
relation, and all are currently typed out by the author and held true by tests
that check them against each other. `DECISIONS.md` says a tool should generate
them. No tool does.

**The domain layer is a schema without behaviour.** `hierarchy`,
`hierarchy_node`, `role_type`, `entity_role`, `state_type`, `entity_state`,
`event_type`, `relationship`, `relationship_type` and `recommendation` are
declared and migrated, and no code reads or writes any of them. Problems are
authored standalone; the Domain screen manages entity types and entities and
stops there. So "domain model generates the problem model" is an arrow on a
diagram, not a path through the code.

**There is no constraint vocabulary.** Temporal, dependency, cardinality and
conditional constraints are all hand-encoded as algebra, once per model. The
`category` field on a constraint is a provenance label the compiler does not
read.

---

## Phase 1 — Soft constraints — **done**

`soft penalty <cost>` on a constraint. The compiler adds a slack column per
direction the row can be missed in, bounds it by what the row can reach, makes
it integral where the row can only be missed by whole numbers, and charges the
objective per unit of violation. Violations are reported as violations, never
as decisions and never absorbed into the activity.

Delivered with `examples/duty_roster.psp`, whose two answers are checked
against arithmetic done by hand, and whose short-handed scenario shows a price
deciding *which* rule gives way.

One limitation worth recording: a penalty is a literal, so a scenario cannot
ask "what if keeping requests mattered more?". Making penalties
parameter-valued is small and worth doing when something needs it.

---

## Phase 2 — Infeasibility diagnosis — **done**

An infeasible solve carries a diagnosis, over the API and the socket alike. The
model is copied, every hard row in the copy is given room to bend and an
indicator that counts when it does, and it is solved twice: fewest rules first,
then least movement. Conflicts come back with the rule, its statement, the
quantity and direction of the shortfall, and the parameters behind it with
their sources; resolutions lead with the exact amount.

Delivered with `examples/ward_cover.psp`, which exists to be infeasible and
whose shortfall drops from two to one to none across its scenarios.

Two things deliberately left for later, both recorded in `DECISIONS.md`:

* It is a **repair**, not an irreducible infeasible subsystem. "Move these,
  this far" is what a planner needs; "these rules contradict each other" is a
  different and also useful answer, and nothing here forecloses adding it.
* Resolutions name the parameters behind a conflict but not how far to move
  each one. Doing that properly means re-solving per parameter, which is what
  `psp/execution/sensitivity.py` already does for feasible models — extending
  it to infeasible ones is the obvious next increment.

---

## Phase 3 — Hierarchy as a construct

Declare the tree once:

```
hierarchy Units by parent {
  operations: organisation, training: organisation,
  ops_north: operations, ops_south: operations, ... }
```

and let the compiler derive ancestor-or-self, shared-leaf overlap, leaf marks,
depth and counts. This is the rollup the whole architecture turns on, stated
once instead of transcribed into four tables.

**Why third.** It is compiler work with no solver risk, and the cost of not
having it has already been paid twice — once for `unit_overlap`, once for
`leaf_count`, each time with a test written to catch the drift that a derived
table invites.

**Done when** `examples/commitment_planning.psp` deletes all five derived
tables and compiles to an **identical IR fingerprint**. That is the only
acceptance test worth having, because it proves the construct means exactly
what the hand-written data meant.

---

## Phase 4 — A constraint vocabulary

Temporal (`before`, `after`, `no_overlap`, `min_gap`, `deadline`,
`recurrence`), dependency (`requires`, `excludes`, `precedes`) and cardinality
(`at least`, `at most`, `exactly`). Each is sugar that lowers to rows the IR
already has, so there is no solver risk and no new mathematics — but the
surface area is wide.

**Build it from the templates, not from the list.** Each form earns its place
by removing hand-written algebra from a model that exists. A vocabulary
designed speculatively is a vocabulary nobody's problem quite fits.

**Done when** the scheduling, routing and timetabling templates are shorter and
still compile to the same models they do today.

---

## Phase 5 — Conditional constraints — *needs a decision first*

`IF / THEN` and `XOR` need indicator variables, which means big-M. That runs
straight into a standing decision: *reject non-linear expressions instead of
linearising them*, on the grounds that an automatic reformulation changes what
the model means.

An auto-generated big-M is exactly that. It picks a constant the author never
chose, and picking it badly is not an error — it is a wrong answer, or a
relaxation so loose the solve never finishes.

**Recommendation:** require the bound from the author, so the constant is
always theirs:

```
if allocate[c, u, p] then load[u, horizon] <= 40 within [0, 500]
```

This is a policy call, not a coding task. Nothing should be built here until it
is settled.

---

## Phase 6 — Binding the domain to the problem

Make the eleven dead tables real: entities, relationships and hierarchies in
the domain generate the index sets, parent relations and tables a problem is
stated over, instead of being retyped into the language.

**Why last.** It is the largest change and the one most likely to be wrong
first time, and it is worth little before Phase 3 and Phase 4 — binding a
domain model to an algebra nobody can read moves the problem rather than
solving it. Afterwards there is something worth binding *to*.

---

## What not to build

**A constraint-propagation engine.** Hierarchical propagation —
`child ≤ parent ≤ ancestor` — is inference, and CP-SAT already does inference
far better than a hand-written engine would. The platform's job is to *state*
the hierarchy correctly, which is Phase 3. Re-implementing propagation above
the solver adds a second thing to be wrong and nothing to be right.

**All seven layers at once.** Evidence and Data exist. Planning is a view over
Problem, not a subsystem. The layers that need work are the three in the
middle, and they are what Phases 1 to 4 are.

**A rules engine, or anything that generates models from natural language.**
The reason this platform can answer "why did the system recommend this?" is
that every number has a traceable origin and every row has an author. Both
would break that, and neither is needed for any problem on the list.

---

## Reading the order

Phases 1, 2 and 3 are each self-contained: stopping after any one leaves the
platform better than before, with nothing half-built. Phase 4 is incremental
and never finishes. Phases 5 and 6 need decisions before they need code.

The rough dependency is that Phase 2 wants Phase 1, and Phase 6 wants Phases 3
and 4. Everything else can move.
