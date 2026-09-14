"""Compiler diagnostics.

Every failure carries the IR location that produced it, so the UI can point at
the offending constraint instead of showing a stack trace.
"""

from __future__ import annotations


class CompileError(Exception):
    def __init__(self, message: str, *, where: str | None = None):
        self.where = where
        self.message = message
        super().__init__(f"{message} (in {where})" if where else message)

    def at(self, where: str) -> CompileError:
        """Return the same error, located.

        The subclass is preserved: a caller that wants to treat a non-linear
        model differently from bad data must still be able to tell them apart
        after the compiler has added location information.
        """
        return type(self)(self.message, where=where)


class NonLinearError(CompileError):
    """A product of two expressions that both carry decision variables."""


class UnboundIndexError(CompileError):
    """An index was referenced that no enclosing binding introduced."""


class DomainError(CompileError):
    """A subscript resolved to an element that is not in the declared set."""
