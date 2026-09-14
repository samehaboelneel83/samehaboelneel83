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

A product of two decision variables could be approximated. It is refused
instead, by name, with the constraint that caused it.

An automatic linearisation changes what the model means. A user who wrote a
quadratic term and received a confident answer to a different question is worse
off than one who received an error.

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
