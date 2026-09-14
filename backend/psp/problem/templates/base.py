"""Problem templates.

A template is a reusable, reviewed encoding of a class of problem: which sets
exist, which decisions are open, which constraints always apply, and — just as
importantly — which assumptions the encoding makes. It turns a table of data
into a defensible Problem Model, so an operator states a problem instead of
building a model.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from psp.problem.spec import ParameterValue, ProblemParameter, ProblemSpec, SourceRef


class TemplateInput(BaseModel):
    """One input a template needs, described well enough to render a form."""

    key: str
    label: str
    kind: str  # "entities" | "table" | "number" | "choice"
    description: str
    required: bool = True
    columns: list[str] = Field(default_factory=list)
    default: Any = None


class ProblemTemplate:
    """Base class for templates."""

    key: str = "abstract"
    title: str = ""
    summary: str = ""
    category: str = "general"
    tags: list[str] = []

    def inputs(self) -> list[TemplateInput]:  # pragma: no cover - interface
        raise NotImplementedError

    def example(self) -> dict:  # pragma: no cover - interface
        raise NotImplementedError

    def build(self, data: dict) -> ProblemSpec:  # pragma: no cover - interface
        raise NotImplementedError

    def describe(self) -> dict:
        return {
            "key": self.key,
            "title": self.title,
            "summary": self.summary,
            "category": self.category,
            "tags": self.tags,
            "inputs": [i.model_dump() for i in self.inputs()],
        }

    # ------------------------------------------------------------- helpers

    @staticmethod
    def require(data: dict, key: str):
        if key not in data:
            raise ValueError(f"missing required input '{key}'")
        return data[key]

    @staticmethod
    def indexed(
        name: str,
        index_sets: list[str],
        rows: dict[str, float] | list[dict],
        *,
        unit: str | None = None,
        description: str | None = None,
        default: float | None = None,
        origin: str = "user_input",
    ) -> ProblemParameter:
        """Build a parameter from ``{"a|b": 3.0}`` or ``[{"index": [...], "value": x}]``.

        Every value is stamped with an origin, because a parameter with no
        recorded source cannot appear in an explanation later.
        """
        values: list[ParameterValue] = []
        if isinstance(rows, dict):
            items = [(k.split("|") if k else [], v) for k, v in rows.items()]
        else:
            items = [(r["index"], r["value"]) for r in rows]
        for index, value in items:
            values.append(
                ParameterValue(
                    index=[str(x) for x in index],
                    value=float(value),
                    origin=SourceRef(source=origin),
                )
            )
        return ProblemParameter(
            name=name, index_sets=index_sets, values=values,
            default=default, unit=unit, description=description,
        )
