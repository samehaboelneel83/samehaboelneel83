# Architecture

## The shape of the thing

```
                    ┌─────────────────────────────┐
                    │          React UI           │
                    │ Domain │ Problem │ Model    │
                    │ Scenario │ Solve │ Results  │
                    └──────────────┬──────────────┘
                                   │  REST + WebSocket
                    ┌──────────────▼──────────────┐
                    │           FastAPI           │
                    └──────────────┬──────────────┘
                                   │
             ┌─────────────────────┼─────────────────────┐
             │                     │                     │
      ┌──────▼──────┐      ┌──────▼──────┐      ┌──────▼──────┐
      │   Domain    │      │   Problem   │      │ Provenance  │
      │   Model     │      │   Model     │      │  & Audit    │
      └──────┬──────┘      └──────┬──────┘      └─────────────┘
             │                    │
             │             ┌──────▼──────┐
             │             │  Model IR   │
             │             │ + Compiler  │
             │             └──────┬──────┘
             │                    │  flat linear system
             │        ┌───────────┼───────────┐
             │   ┌────▼────┐ ┌────▼────┐ ┌────▼─────┐
             │   │OR-Tools │ │ HiGHS   │ │NetworkX  │
             │   │ CP-SAT  │ │ LP/MIP  │ │  flow    │
             │   └─────────┘ └─────────┘ └──────────┘
             │        each in its own worker process
       ┌─────▼─────────────────────────────────────┐
       │                PostgreSQL                 │
       │  JSONB │ ltree │ PostGIS │ partitioning   │
       └───────────────────────────────────────────┘
```

## Why an IR at all

A platform that calls OR-Tools directly is an application that uses an
optimisation library. Every problem it can state is a problem OR-Tools can
express, every model it stores is in OR-Tools' vocabulary, and replacing the
engine means rewriting the problems.

The IR inverts that. A problem is stated once, in business terms. The compiler
lowers it to a normal form. Adapters lower the normal form to engines. Swapping
an engine touches one adapter.

This is not theoretical here. `transportation` compiles once and is solved
either by network simplex or by a MIP engine, depending on whether the compiler
can verify the network structure. The two return the same optimum. Nothing in
the problem model knows either engine exists.

### The IR is indexed, not flat

The IR keeps sets, indexed parameters and constraint *families*. Flattening to a
sparse matrix happens later, in one pass, and every generated row keeps a
back-pointer to the family and index tuple it came from.

A flat matrix alone would be enough to solve. It would not be enough to explain.
`meet_demand[base_2] is binding` is an answer; `row 7 is binding` is not.

### One expression language, three jobs

The same expression nodes serve arithmetic inside constraints, index arithmetic
inside subscripts (`start[j, t - duration[j]]`), and the comparisons in `where`
filters. Three separate mini-languages would be three things to learn, three
parsers and three sets of bugs.

The language is deliberately linear. A product of two expressions that both
carry decision variables is rejected at compile time, by name, with the
constraint that caused it. Silently linearising it would be a wrong answer
delivered confidently, which is the worst failure mode this system has.

## Determinism

The same problem and scenario always compile to the same IR, with the same row
and column order. `IRModel.fingerprint()` is a content hash of that, and a
`model_version` row stores it. A run points at a version, never at a mutable
problem.

This is what lets the platform refuse to explain a solution whose problem has
since been edited: it compares fingerprints and returns 409 rather than
interpreting old numbers against a new model.

## Solver execution runs out of process

Not an optimisation — a necessity, and then three other things for free.

OR-Tools and `highspy` each bundle their own build of HiGHS. Importing both into
one interpreter raises `ImportError: undefined symbol` from whichever loses the
race. They are physically incapable of sharing a process.

So every solve runs in `python -m psp.solvers.worker`, with the request and
result passed as files rather than pipes (an engine writing to stdout cannot
then corrupt the result). Which also means:

* a solver that segfaults or exhausts memory takes down its own process, not the API;
* the wall-clock limit is enforced from outside, where the engine cannot evade it;
* the API process never loads an engine at all, so eligibility is decided from a
  static capability table and `/api/solvers` costs nothing.

## Solver selection

Capability-driven, most specialised engine first, and recorded. Every run stores
which engines were considered and why each was or was not eligible.

An explicitly requested engine is **never** silently substituted. If it cannot
take the model the request fails with the reason and the list of engines that
would have worked. A run record naming an engine the user did not choose is
worse than no answer, because nothing downstream would reveal the substitution.

## Explanation without a language model

For a given decision the platform already holds everything needed:

1. the constraint rows the variable appears in, and its coefficient in each;
2. which of those are tight, computed from the solution vector;
3. what one more unit of headroom is worth, from LP duality where it exists;
4. the parameters feeding each constraint, from a structural walk of the IR;
5. where each parameter value came from, recorded when it was set.

The narrative is templated from that, not generated. In an audited setting a
sentence that can drift from the numbers is worse than no sentence.

Where duals do not exist, the platform says *why* — an integer model has none to
give, a combinatorial engine does not compute them — because those two have
different remedies ("use sensitivity analysis" versus "re-solve with HiGHS").

## Readable keys

A key like `place[algo_y2,hall_a,23]` is right for the compiler and useless to
the person reading the timetable. Slot 23 has to stay numeric — the compiler
does arithmetic on it to work out what is running at a given moment — and has
to read `Thu 13:45` in the answer.

So a set may carry a label map, and the rendering layer applies it to decisions,
binding constraints, explanations and generated rows alike. Labels live on the
IR rather than being looked up from the problem at render time, so a model
stored today still explains readably in a year. They are presentation only: the
raw key stays the identifier in `Solution.values`, in provenance rows and in the
explain API, so nothing downstream has to parse a display string back into an
index.

## Data model

Eight layers: IAM, DOMAIN, PROBLEM, MODEL, SOLVER, EXECUTION, SOLUTION,
PROVENANCE. Two things are first-class on purpose.

**Model versioning.** A version is immutable and carries its IR fingerprint,
compilation record and flat-model artifact. Reproducing a six-month-old
recommendation is a lookup, not an archaeology exercise.

**Provenance.** Sources, facts, evidence and audit events are tables, not log
lines, because "why did the system recommend this?" has to be answerable by
someone who was not there.

The ORM uses portable JSON so it runs on SQLite as well as PostgreSQL. The
PostgreSQL migrations upgrade those columns to JSONB with GIN indexes, convert
path columns to `ltree`, add a PostGIS `geography` column with a trigger keeping
it in step with the lat/long pair, and range-partition the three tables that
grow without bound.

## Templates are the product

A template is a reviewed encoding of a class of problem. It knows which
constraints always apply, and — more importantly — which assumptions the
encoding makes.

Every template states its assumptions explicitly: that value scales linearly
with activity, that tasks cannot be pre-empted, that MTZ routing is practical to
about twenty-five stops. An operator who never opens a solver still sees what
the model committed them to, and an explanation repeats those assumptions
alongside the recommendation.

Templates also refuse impossible data before compiling: demand exceeding fleet
capacity, a horizon shorter than the longest task. Failing at the point where
the cause is obvious beats failing as "infeasible" three layers down.

## Simulation alongside optimisation

Optimisation answers "what is the best plan under these assumptions?".
Simulation answers "what happens to this plan when arrivals are random?". A
schedule optimal under average demand can be fragile under realistic demand, and
only the second will say so. The SimPy queue model exists to stress-test the
first kind of answer.

## Authentication

Keycloak owns identity. The platform verifies tokens against the realm's JWKS
and reads roles; it never sees a password and never issues a token.

With no issuer configured the API runs open. That is correct for a first run and
wrong everywhere else, so `/api/health` reports it, the API logs it at startup,
and the UI shows a badge. An open deployment should not be able to go unnoticed.
