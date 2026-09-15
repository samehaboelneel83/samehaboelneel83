"""Diagnostics for the problem language.

A modelling language is only easy if its errors are. Every failure carries the
line and column it happened at, and renders the offending line with a caret
under it, because "unexpected token at 14:7" sends the author hunting and
``sum(... for d in Destinatons)`` with a caret under the typo does not.
"""

from __future__ import annotations


class DslError(Exception):
    """A problem in the source text, located."""

    def __init__(
        self,
        message: str,
        line: int = 0,
        column: int = 0,
        source: str | None = None,
        hint: str | None = None,
    ):
        self.message = message
        self.line = line
        self.column = column
        self.source = source
        self.hint = hint
        super().__init__(self.render())

    def render(self) -> str:
        parts = [f"line {self.line}, column {self.column}: {self.message}"
                 if self.line else self.message]
        excerpt = self.excerpt()
        if excerpt:
            parts.append(excerpt)
        if self.hint:
            parts.append(f"hint: {self.hint}")
        return "\n".join(parts)

    def excerpt(self) -> str | None:
        """The offending line with a caret under the column."""
        if not self.source or self.line < 1:
            return None
        lines = self.source.splitlines()
        if self.line > len(lines):
            return None
        text = lines[self.line - 1]
        gutter = f"{self.line:>4} | "
        caret = " " * (len(gutter) + max(0, self.column - 1)) + "^"
        return f"{gutter}{text}\n{caret}"

    def to_dict(self) -> dict:
        return {
            "message": self.message,
            "line": self.line,
            "column": self.column,
            "hint": self.hint,
            "excerpt": self.excerpt(),
        }


class LexError(DslError):
    """The text could not be broken into tokens."""


class ParseError(DslError):
    """The tokens do not form a valid declaration."""


class ResolveError(DslError):
    """The declarations are well-formed but refer to something that is not there."""


def did_you_mean(name: str, candidates) -> str | None:
    """Suggest the closest known name, when one is close enough to be a typo."""
    import difflib

    matches = difflib.get_close_matches(name, list(candidates), n=1, cutoff=0.7)
    return f"did you mean '{matches[0]}'?" if matches else None
