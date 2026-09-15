"""The problem language: author a problem class as text, not Python."""

from psp.dsl.errors import DslError, LexError, ParseError, ResolveError
from psp.dsl.lower import lower
from psp.dsl.parser import parse
from psp.dsl.writer import write_problem
from psp.problem.spec import ProblemSpec


def parse_problem(text: str, origin: str = "dsl") -> ProblemSpec:
    """Read a problem from source text.

    Raises :class:`DslError` — located, with the offending line and a hint —
    for anything the text gets wrong.
    """
    return lower(parse(text), source=text, origin=origin)


__all__ = [
    "DslError", "LexError", "ParseError", "ResolveError",
    "ProblemSpec", "parse", "lower", "parse_problem", "write_problem",
]
