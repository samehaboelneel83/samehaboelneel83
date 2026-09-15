# Decisions

Choices made deliberately against the more obvious option, with the reasoning,
so they can be revisited on purpose rather than by accident.

---

### Build the Model IR rather than adopt MiniZinc, Timefold or Essence

Adopting a modelling framework makes it the heart of the platform. Every problem
becomes expressible only in its vocabulary, every stored model is in its format,
and its lifecycle becomes the platform's lifecycle.

A custom IR is more work and less expressive on day one. In exchange the
business model is independent of every engine, the compiler is small enough to
audit, and provenance can be threaded through compilation rather than bolted on.

**Revisit when** a problem class genuinely cannot be expressed linearly. MiniZinc
then belongs *below* the IR as another adapter, not above it as the front door.

---

### Time-indexed scheduling rather than interval variables

CP-SAT's interval variables and `NoOverlap` are the natural way to schedule, and
they are strictly better at it.

They are also solver-specific. Using them would mean the scheduling template can
only ever run on CP-SAT, and the IR would need a concept that exactly one engine
understands. The time-indexed formulation costs variables but stays linear, so
the same model runs on CP-SAT or HiGHS and every constraint remains explainable.

**Revisit when** a scheduling instance is too large for the time-indexed form.
The right response is a second template with a declared structure hint and a
CP-SAT adapter that recognises it — the same mechanism network simplex already
uses — not a leak of interval variables into the IR.

---

### MTZ subtour elimination rather than lazy constraints

Lazy constraint callbacks give far better routing performance and tie the model
to a specific solver's callback API.

MTZ is compact, linear and weak. It is practical to roughly twenty-five stops,
which the template states as an assumption rather than leaving to be discovered
in production.

**Revisit when** routing is a primary use case. That is the point to add
Timefold or an OR-Tools routing adapter behind a structure hint.

---

### Every solve in its own process

Forced: OR-Tools and `highspy` each bundle HiGHS and cannot share an
interpreter. But it is what should have been chosen anyway — crash containment,
an inescapable wall-clock limit, and an API process that never loads an engine.

The cost is roughly 200ms of interpreter start-up per solve. For problems where
that matters, the answer is a persistent worker pool, not a shared process.

---

### One expression language for arithmetic, subscripts and filters

Index arithmetic (`start[j, t - duration[j]]`) could have had its own small
syntax. It does not, because three languages means three parsers, three sets of
edge cases and three things to learn. Index positions are ordinary expressions
required to evaluate to a constant.

---

### Reject non-linear expressions instead of linearising them

A product of two decision variables could be approximated. Outside an objective
it is refused instead, by name, with the constraint that caused it.

An automatic linearisation changes what the model means. A user who wrote a
quadratic term and received a confident answer to a different question is worse
off than one who received an error.

---

### A quadratic objective, but never a quadratic row

Degree two is allowed in an objective and refused everywhere else.

The reason is not squeamishness about non-linearity; it is that the two are
different kinds of problem. A quadratic *objective* over a linear feasible
region is still a convex program with a dual, so every row stays a sparse line a
shadow price can be attached to and an explanation can cite. A quadratic *row*
bends the feasible region, and the provenance chain — which explains a decision
by naming the rows that bind on it and what one more unit of each would be
worth — would be explaining something it no longer describes.

So the boundary is drawn where the explanation stops being true, not where the
mathematics stops being convenient. It also happens to be the boundary of what
the engines here can do, which is a coincidence worth not relying on.

---

### Two mechanisms for degree two, chosen by capability

A quadratic objective reaches an engine one of two ways. HiGHS takes a Hessian
and minimises it as a convex program. CP-SAT takes an auxiliary variable per
product, constrained to equal it, and searches. The model says nothing about
which; the flat model carries degree-two terms and the registry picks.

The consequence is a gap, and it is stated rather than hidden. HiGHS has no
mixed-integer quadratic mode and CP-SAT needs a fully discrete model, so a
quadratic objective over *both* continuous and discrete variables has no engine
here. The refusal names both shapes that do work.

Convexity is decided by the compiler, not discovered by the engine. HiGHS
answers a non-convex quadratic with a bare error and no model status, which
would surface as "solver error" long after the objective lost its name. The
Hessian's smallest eigenvalue is checked while the objective is still called
`imbalance`, and above a thousand quadratically-involved variables the answer
becomes "not established" rather than a claim the compiler did not check.

CP-SAT is not asked about convexity at all — nothing is being minimised by
convex optimisation there, so a non-convex discrete objective is ordinary work.

---

### A rule is hard unless someone prices breaking it

Soft constraints are opt-in, by writing `soft penalty 20` on the rule. There is
no global "relax when infeasible" switch and no default penalty.

A default would decide, on the modeller's behalf, that every rule is negotiable
— and the first time it mattered, a plan would come back having quietly broken
a regulation because breaking it was cheaper than the alternative nobody had
priced. Hardness is the safe default because its failure mode is an error
message, and softness is the unsafe one because its failure mode is a confident
answer.

A penalty of zero is refused for the same reason: a rule that is free to break
is not a rule, and writing one is more likely to be a mistake than an intent.

---

### Violation is a distance, not a flag

A broken rule is charged per unit it is missed by, not once for being broken.

Charging per rule makes the second unit of violation free, which is exactly
backwards: a roster missing one shift and a roster missing four would cost the
same, and the optimiser would have no reason to prefer the first. Per-unit
charging also gives the number a meaning outside the objective — "short by
three" is something a planner can act on.

The consequence is that an equality needs two slack columns rather than one,
since it can be missed in either direction and both misses are distances.

---

### The slack a soft constraint needs is bounded and, where it can be, integral

The obvious implementation of a soft constraint adds a continuous, unbounded
slack column. Either property would quietly cost a discrete model its engine:
CP-SAT requires a fully discrete model and a finite bound on every integer, so
the first rule to become soft would have moved the model to HiGHS without
anyone asking.

So the column is integral when the row can only be missed by whole numbers, and
its bound is what the row's own columns can actually reach. Both are derived,
neither is asked of the author, and the test that holds them is that the duty
roster still chooses CP-SAT.

---

### Diagnose an infeasible model by repair, not by irreducibility

Faced with no plan, the platform reports the fewest rules that would have to
give and by how much — not an irreducible infeasible subsystem.

The two answer different questions. An IIS says "these rules are mutually
contradictory", which is a proof; a repair says "move these, this far", which
is a plan. A planner is trying to get a roster out, so the second is the one
worth computing, and it comes with quantities the first cannot give.

Fewest rules comes first, then least movement, because a list of thirty rules
each off by a fraction is not something anyone can act on. It has a known
failure mode — one rule relaxed enormously can beat two relaxed slightly — and
that is why the quantity is always reported beside the rule rather than left
implicit.

The indicator that makes "fewest" askable is a big-M, which this platform
otherwise refuses to generate. It is allowed here because the constant is the
row's own reach, computed from the columns the row is built from, and because
it lives in a throwaway copy that is never solved for an answer.

---

### Diagnosis never touches the model it diagnoses

The elastic model is built as a copy, used, and dropped. Nothing in the stored
problem becomes soft, and the run that failed stays failed.

The alternative — relaxing in place and returning the relaxed answer — would
mean a plan could come back having quietly broken a rule the author wrote as
absolute, with only a status field to say so. That is the failure mode this
whole platform is built to avoid, and it is not worth one fewer object.

---

### An explicitly requested solver is never substituted

Falling back to a capable engine when the requested one cannot take the model
would always return an answer. It would also write a run record naming an engine
the user did not choose, with nothing downstream revealing it. The request fails
instead, with the reason and the list of engines that would have worked.

---

### Refuse to explain a solution whose problem has changed

Explaining an old solution against an edited problem would mostly work and
occasionally produce a confident, wrong account of a past decision. The
fingerprint comparison makes that impossible: the API returns 409 and says to
re-solve.

---

### Report each objective in its own direction

The flat model minimises a single weighted sum, with maximisations negated.
Reporting that number is how a report ends up showing `-4500` for a plan worth
4500. Each objective component is re-evaluated from the solution vector and
reported with its own sense, weight and unit. There is a test for exactly this.

---

### Name the actual reason duals are missing

"No shadow prices" has several causes needing different responses: an integer
model has none to give (use sensitivity analysis), a combinatorial engine does
not compute them (re-solve with HiGHS), a multi-objective model's dual refers to
the composite. The server computes which one applies; the UI does not guess.

---

### Hierarchies are precomputed relations, not expressions over the tree

A model that spans a hierarchy needs to know which nodes cover which, and how
much two of them overlap. Both are derivable from the parent links, and a rule
can be written to derive them inline — the compiler flattens either form to the
same rows. But deriving `overlap` inline puts a leaf loop inside every rule that
mentions two units, so the terms enumerated go from one per pair of units to one
per pair and every leaf between them. On the seven-unit example that is three
seconds of flattening; on a real organisation it is the difference between a
model that builds and one that does not.

So a hierarchy enters a problem as tables — `covers` for ancestor-or-self,
`overlap` for shared leaves — and the rules read them.

What that reasoning never justified was making the author write them out. The
language now takes the tree and derives the tables from it, so the edges are
the only thing anyone maintains. The tables are still tables, for the reason
above; what has gone is the copying, and with it the class of bug where a
`leaf_count` of 4 outlives the fourth leaf.

The derived tables take the author\'s names rather than canonical ones. A unit
tree and a specialty tree in the same model each need a `covers`, and only the
author knows which word their domain uses — which is also what let the
construct be checked against the hand-written tables it replaced, rather than
merely against itself.

---

### Weights that encode a precedence are derived, not tuned

Where one objective must never be traded against another, the weights that say
so are worked out from the model's own numbers and the working is kept beside
them: the cheapest thing the first objective can lose, against the widest span
of the second. In the commitment plan that is 20 x 1000 against 4 x 40^2, and
the test asserts the inequality rather than the figures the solver returns.

The reason is that a weight tuned until the answer looks right is a weight that
will stop being right when the data moves, silently, in a direction nobody is
watching. The reason it matters here is that squaring a term moves it by orders
of magnitude: balance as a range spans about ten, and as a variance, thousands.
A measure swapped without retuning would have kept its name, kept its place in
the objective, and quietly started deciding which commitments get dropped.

---

### Portable JSON in the ORM, PostgreSQL features in migrations

Modelling JSONB, ltree and PostGIS directly in the ORM would make PostgreSQL a
hard dependency of the test suite and of a first run. The ORM stays portable and
the migrations add the real thing, so `pytest` needs no database server while
production loses nothing.

---

### Not yet: Neo4j, Kafka, RDF/OWL, an LLM

Each solves a problem this platform does not have on day one.

* **Neo4j** — the relationship queries in scope are shallow. `ltree` plus a
  closure table answers them with one fewer datastore to operate.
* **Kafka** — there is one writer and no streaming consumer. The `event` table
  is partitioned and ready if that changes.
* **RDF/OWL** — worth the cost only when ontologies are exchanged with an
  outside party. The entity/attribute/relationship tables can be projected to
  RDF when something needs to consume it.
* **An LLM** — explanation is the one place it would be tempting, and the one
  place it must not be used. A recommendation in an operational setting has to
  be reproducible and defensible. Templated narration from binding constraints
  and shadow prices is both; generated prose is neither.
