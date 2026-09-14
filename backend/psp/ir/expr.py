"""The expression language of the Model IR.

One small algebraic language serves three purposes, which keeps the compiler
honest: arithmetic inside constraints, index arithmetic inside subscripts
(``x[j, t - duration[j]]``), and the scalar comparisons used by ``where``
filters.  Every node is a pydantic model, so an entire model round-trips
through JSON — that is how models are stored, versioned, diffed and shipped
across the process boundary to a solver worker.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

Scalar = Union[float, str]


class Const(BaseModel):
    """A numeric literal."""

    op: Literal["const"] = "const"
    value: float


class Lit(BaseModel):
    """A set-element literal, e.g. the label ``"depot"``. Index positions only."""

    op: Literal["lit"] = "lit"
    value: str


class IdxRef(BaseModel):
    """The value currently bound to an iteration index.

    In an integer set the value is the element's number, so it takes part in
    arithmetic; in a label set it stays a label and may only be compared.
    """

    op: Literal["idx"] = "idx"
    name: str


class ParamRef(BaseModel):
    """A lookup into a parameter table. ``index`` entries must be constant."""

    op: Literal["param"] = "param"
    name: str
    index: list["Expr"] = Field(default_factory=list)


class VarRef(BaseModel):
    """A reference to a decision variable. ``index`` entries must be constant."""

    op: Literal["var"] = "var"
    name: str
    index: list["Expr"] = Field(default_factory=list)


class Neg(BaseModel):
    op: Literal["neg"] = "neg"
    arg: "Expr"


class Add(BaseModel):
    op: Literal["add"] = "add"
    args: list["Expr"]


class Sub(BaseModel):
    op: Literal["sub"] = "sub"
    args: list["Expr"]


class Mul(BaseModel):
    """Product. At most one factor may carry decision variables."""

    op: Literal["mul"] = "mul"
    args: list["Expr"]


class Div(BaseModel):
    """Quotient. The divisor must be constant."""

    op: Literal["div"] = "div"
    args: list["Expr"]


class Binding(BaseModel):
    """``index`` ranges over the elements of ``set``."""

    index: str
    set: str


class Sum(BaseModel):
    """Aggregation over the cross-product of ``over``, filtered by ``where``."""

    op: Literal["sum"] = "sum"
    over: list[Binding]
    body: "Expr"
    where: "Pred | None" = None


Expr = Annotated[
    Union[Const, Lit, IdxRef, ParamRef, VarRef, Neg, Add, Sub, Mul, Div, Sum],
    Field(discriminator="op"),
]

CmpOp = Literal["eq", "ne", "lt", "le", "gt", "ge"]


class Cmp(BaseModel):
    """A comparison between two constant-evaluable expressions."""

    pred: Literal["cmp"] = "cmp"
    op: CmpOp
    lhs: Expr
    rhs: Expr


class And(BaseModel):
    pred: Literal["and"] = "and"
    args: list["Pred"]


class Or(BaseModel):
    pred: Literal["or"] = "or"
    args: list["Pred"]


class Not(BaseModel):
    pred: Literal["not"] = "not"
    arg: "Pred"


Pred = Annotated[Union[Cmp, And, Or, Not], Field(discriminator="pred")]

RelOp = Literal["le", "ge", "eq"]


class Rel(BaseModel):
    """A relational constraint body: ``lhs (<=|>=|==) rhs``."""

    op: RelOp
    lhs: Expr
    rhs: Expr


for _m in (ParamRef, VarRef, Neg, Add, Sub, Mul, Div, Sum, Cmp, And, Or, Not, Rel):
    _m.model_rebuild()
