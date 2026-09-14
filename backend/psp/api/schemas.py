"""Request and response shapes for the HTTP API."""

from __future__ import annotations

from pydantic import BaseModel, Field

from psp.problem.spec import ProblemSpec


class InstantiateRequest(BaseModel):
    template: str
    data: dict = Field(default_factory=dict)
    save: bool = True


class ProblemSummary(BaseModel):
    id: str
    key: str
    name: str
    description: str | None = None
    template_key: str | None = None
    status: str
    variables: int = 0
    constraints: int = 0
    scenarios: list[str] = Field(default_factory=list)
    updated_at: str | None = None


class SolveRequest(BaseModel):
    scenario: str | None = None
    solver: str | None = None
    time_limit_seconds: float | None = None
    relative_gap: float | None = None
    threads: int = 1
    seed: int = 0


class CompareRequest(BaseModel):
    scenarios: list[str | None] = Field(default_factory=lambda: [None])
    solver: str | None = None
    time_limit_seconds: float | None = None


class SensitivityRequest(BaseModel):
    method: str = "one_at_a_time"
    parameters: list[str] | None = None
    multipliers: list[float] = Field(default_factory=lambda: [0.9, 1.1])
    scenario: str | None = None
    max_solves: int = 40
    time_limit_seconds: float | None = None


class SaveProblemRequest(BaseModel):
    spec: ProblemSpec
