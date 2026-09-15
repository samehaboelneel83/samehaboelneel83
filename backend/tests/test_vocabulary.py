"""Saying what kind of rule a rule is.

Two shapes recur across every model here: a sum of things that must not happen,
held at zero, and a sum held against a plain number. Written as algebra they are
encodings — a zero on the right-hand side is not a statement that something is
forbidden, it is a way of arranging one. These forms say it instead, and build
exactly the same rows, which is what the tests below check.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

from psp.compiler import compile_and_flatten
from psp.dsl import ParseError, parse_problem
from psp.ir.dsl import at_least, at_most, eq, exactly, ge, i, le, never, num, total, v
from psp.problem.templates.registry import available, get

EXAMPLES = pathlib.Path(__file__).resolve().parents[2] / "examples"

HEAD = """
problem q "Q"
  "Counting things."
set Crews = alpha, bravo of crew
set Shifts = morning, night of shift
param qualified[Crews, Shifts] default 1 = { (alpha, night): 0 }
var assign[Crews, Shifts] binary means "Put this crew on this shift"

constraint rule "A rule"
"""
TAIL = """
minimize used "Shifts worked" unit "shifts":
  sum(assign[c, s] for c in Crews, s in Shifts)
"""


def model(body: str):
    return compile_and_flatten(parse_problem(HEAD + body + "\n" + TAIL))


@pytest.mark.parametrize(
    "phrase,algebra",
    [
        ("  never assign[c, s] for c in Crews, s in Shifts where qualified[c, s] == 0",
         "  sum(assign[c, s] for c in Crews, s in Shifts where qualified[c, s] == 0) == 0"),
        ("  forall s in Shifts:\n    at most 1 of assign[c, s] for c in Crews",
         "  forall s in Shifts:\n    sum(assign[c, s] for c in Crews) <= 1"),
        ("  forall s in Shifts:\n    at least 1 of assign[c, s] for c in Crews",
         "  forall s in Shifts:\n    sum(assign[c, s] for c in Crews) >= 1"),
        ("  forall s in Shifts:\n    exactly 2 of assign[c, s] for c in Crews",
         "  forall s in Shifts:\n    sum(assign[c, s] for c in Crews) == 2"),
    ],
)
def test_each_phrase_builds_the_model_its_algebra_builds(phrase, algebra):
    """Sugar, in the strict sense: the same model, spelled differently."""
    assert model(phrase).ir.fingerprint() == model(algebra).ir.fingerprint()


def test_the_phrase_may_be_broken_across_lines():
    """The long form gets line breaks free, being inside brackets. This one is
    not, so the continuation has to be arranged for."""
    wrapped = model(
        "  never assign[c, s]\n"
        "    for c in Crews, s in Shifts\n"
        "    where qualified[c, s] == 0"
    )
    inline = model(
        "  never assign[c, s] for c in Crews, s in Shifts where qualified[c, s] == 0"
    )
    assert wrapped.ir.fingerprint() == inline.ir.fingerprint()


def test_a_miswritten_count_says_what_was_expected():
    with pytest.raises(ParseError) as caught:
        model("  at fewest 1 of assign[c, s] for c in Crews, s in Shifts")
    assert "at most" in str(caught.value) and "at least" in str(caught.value)


def test_the_words_are_not_reserved_elsewhere():
    """`never`, `at`, `exactly`, `most` and `least` are matched where a counted
    rule can start, not taken out of the language. A model may still call
    something `count` or `exactly`."""
    spec = parse_problem("""
problem q "Q"
  "Words that are not keywords."
set Crews = alpha, bravo of crew
param exactly[Crews] default 1 = { alpha: 2 }
param never[Crews] default 0 = { bravo: 1 }
var at[Crews] binary means "Pick"

constraint rule "A rule"
  sum(exactly[c] * at[c] + never[c] * at[c] for c in Crews) <= 3

minimize z "z" unit "u":
  sum(at[c] for c in Crews)
""")
    assert {p.name for p in spec.parameters} == {"exactly", "never"}


def test_the_builders_and_the_phrases_agree():
    """Both surfaces exist — templates are Python, problems are text — and a
    vocabulary that meant different things in each would be worse than none."""
    body = v("assign", i("c"), i("s"))
    assert never(body, c="Crews").model_dump() == eq(
        total(body, c="Crews"), num(0)
    ).model_dump()
    assert at_most(1, body, c="Crews").model_dump() == le(
        total(body, c="Crews"), num(1)
    ).model_dump()
    assert at_least(2, body, c="Crews").model_dump() == ge(
        total(body, c="Crews"), num(2)
    ).model_dump()
    assert exactly(3, body, c="Crews").model_dump() == eq(
        total(body, c="Crews"), num(3)
    ).model_dump()


# ------------------------------------------------- what the rewrite must keep


TEMPLATE_FINGERPRINTS = {
    "resource_allocation": "83da23edd124",
    "assignment": "ecfacbc6b890",
    "scheduling": "a3e648878363",
    "transportation": "ab5d6d9f6973",
    "vehicle_routing": "51268e1e808b",
    "lecture_timetabling": "352a4230d7b4",
}


def build(key: str):
    template = get(key)
    example = template.example()
    return template.build(example.get("data", example))


@pytest.mark.parametrize("key", sorted(TEMPLATE_FINGERPRINTS))
def test_rewriting_the_templates_moved_no_model(key):
    """Nine constraint families in the timetable and two in the routing model
    were rewritten in the new vocabulary. These are the fingerprints from
    before, and the point of the exercise is that they did not move."""
    assert compile_and_flatten(build(key)).ir.fingerprint()[:12] == TEMPLATE_FINGERPRINTS[key]


def test_every_template_compiles_the_same_way_in_every_process():
    """A fingerprint that changes between runs is not a fingerprint.

    This is here because one did. The timetabling template iterated a Python
    set of strings into a parameter's keys, and since a set of strings orders
    differently in each process, the same timetable compiled to a different
    fingerprint every time — which would have meant a new stored model version
    per run and a provenance chain that could not say two runs shared a model.
    An in-process repeat cannot catch it; only a second interpreter can.
    """
    script = (
        "import sys; sys.path.insert(0, '.');"
        "from psp.compiler import compile_and_flatten;"
        "from psp.problem.templates.registry import available, get;"
        "print(' '.join("
        "compile_and_flatten(get(k).build(get(k).example().get('data', get(k).example())))"
        ".ir.fingerprint() for k in available()))"
    )
    root = pathlib.Path(__file__).resolve().parents[1]
    runs = []
    for seed in ("1", "7"):
        finished = subprocess.run(
            [sys.executable, "-c", script], cwd=root, capture_output=True, text=True,
            env={**os.environ, "PYTHONHASHSEED": seed}, check=True,
        )
        runs.append(finished.stdout.strip())
    assert runs[0] == runs[1], "a template compiles differently depending on the process"


@pytest.mark.parametrize(
    "name", ["commitment_planning.psp", "duty_roster.psp"]
)
def test_the_examples_that_forbid_things_now_say_so(name):
    source = (EXAMPLES / name).read_text()
    assert "never " in source, "the example was not moved to the new vocabulary"
    spec = parse_problem(source)
    assert compile_and_flatten(spec).flat.constraints, "it no longer compiles to rows"
