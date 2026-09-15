"""The parse tree.

Deliberately *unresolved*: a bare word is just a ``Word`` here, because whether
``depot_north`` is a set element, a scalar parameter or a bound index depends on
declarations that may appear later in the file. Resolution happens in
:mod:`psp.dsl.lower`, which has the whole program in hand and can say which one
was meant — and suggest the right spelling when none of them was.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Located:
    line: int = 0
    column: int = 0


# ------------------------------------------------------------- expressions


@dataclass
class Num(Located):
    value: float = 0.0


@dataclass
class Text(Located):
    """A quoted word: always a set element, never a name to resolve."""

    value: str = ""


@dataclass
class Word(Located):
    """A bare identifier, resolved later."""

    name: str = ""


@dataclass
class Lookup(Located):
    """``name[a, b]`` — a parameter or variable reference."""

    name: str = ""
    args: list[Any] = field(default_factory=list)


@dataclass
class Unary(Located):
    op: str = "-"
    arg: Any = None


@dataclass
class Arith(Located):
    """``a + b + c`` as one node, not nested pairs.

    The Python builders produce n-ary sums, so flattening a left-associative
    chain here is what lets a model survive a round trip through text with the
    same fingerprint. Safe for + - * (the evaluator folds left to right); ``/``
    stays binary."""

    op: str = "+"
    args: list[Any] = field(default_factory=list)
    grouped: bool = False
    """True when the node came from parentheses, which stops a following
    operand being merged into it: ``(a + b) + c`` is not ``a + b + c``."""


@dataclass
class Binding(Located):
    index: str = ""
    set_name: str = ""


@dataclass
class Aggregate(Located):
    """``sum(body for i in S, j in T where pred)``."""

    body: Any = None
    bindings: list[Binding] = field(default_factory=list)
    where: Any = None


# -------------------------------------------------------------- predicates


@dataclass
class Compare(Located):
    op: str = "=="
    lhs: Any = None
    rhs: Any = None


@dataclass
class Logical(Located):
    op: str = "and"  # and | or
    args: list[Any] = field(default_factory=list)
    grouped: bool = False


@dataclass
class Negate(Located):
    arg: Any = None


# ------------------------------------------------------------ declarations


@dataclass
class SetDecl(Located):
    from_entity_type: str = ""
    name: str = ""
    kind: str = "label"
    elements: list[str] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)
    entity_type: str | None = None
    description: str | None = None


@dataclass
class ParamDecl(Located):
    name: str = ""
    index_sets: list[str] = field(default_factory=list)
    default: float | None = None
    unit: str | None = None
    description: str | None = None
    from_attribute: str = ""
    values: list[tuple[list[str], float]] = field(default_factory=list)


@dataclass
class HierarchyDecl(Located):
    name: str = ""
    from_relationship: str = ""
    direction: str = "parent"
    derived: dict[str, str] = field(default_factory=dict)
    parent: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class VarDecl(Located):
    name: str = ""
    index_sets: list[str] = field(default_factory=list)
    kind: str = "continuous"
    lb: float = 0.0
    ub: float | None = None
    description: str | None = None
    meaning: str | None = None


@dataclass
class ConstraintDecl(Located):
    name: str = ""
    statement: str = ""
    category: str = "operational"
    penalty: float | None = None
    when: Any = None
    when_is: float = 1.0
    rationale: str | None = None
    forall: list[Binding] = field(default_factory=list)
    where: Any = None
    op: str = "le"
    lhs: Any = None
    rhs: Any = None


@dataclass
class ObjectiveDecl(Located):
    name: str = ""
    statement: str = ""
    sense: str = "minimize"
    weight: float = 1.0
    unit: str | None = None
    expr: Any = None


@dataclass
class AssumeDecl(Located):
    key: str = ""
    statement: str = ""
    rationale: str | None = None
    affects: list[str] = field(default_factory=list)


@dataclass
class Override(Located):
    parameter: str = ""
    index: list[str] | None = None  # None means every index of the parameter
    value: float | None = None
    scale: float | None = None


@dataclass
class ScenarioDecl(Located):
    key: str = ""
    name: str = ""
    description: str | None = None
    overrides: list[Override] = field(default_factory=list)


@dataclass
class StructureDecl(Located):
    kind: str = ""
    fields: dict[str, str] = field(default_factory=dict)


@dataclass
class Program(Located):
    key: str = ""
    name: str = ""
    description: str | None = None
    declarations: list[Any] = field(default_factory=list)
