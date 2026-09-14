"""Simulation endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from psp.simulation.queueing import SimulationRequest, simulate

router = APIRouter(prefix="/simulate", tags=["simulation"])


class CapacitySweepRequest(BaseModel):
    base: SimulationRequest
    server_counts: list[int] = Field(default_factory=lambda: [2, 3, 4, 5])


@router.post("/queue")
def queue(request: SimulationRequest) -> dict:
    return simulate(request).model_dump(mode="json")


@router.post("/queue/sweep")
def queue_sweep(request: CapacitySweepRequest) -> dict:
    """Run the same arrival process against several capacities.

    This is the question an optimisation run cannot answer on its own: the plan
    may be optimal on average, but the sweep shows where waiting time turns from
    manageable into unbounded.
    """
    rows = []
    for count in sorted(set(request.server_counts)):
        scenario = request.base.model_copy(update={"servers": count})
        result = simulate(scenario)
        rows.append(
            {
                "servers": count,
                "utilisation": result.server_utilisation,
                "mean_wait_minutes": result.mean_wait_minutes,
                "p95_wait_minutes": result.p95_wait_minutes,
                "mean_queue_length": result.mean_queue_length,
                "unserved_at_close": result.unserved_at_close,
                "stable": not result.notes,
            }
        )
    return {"sweep": rows, "replications": request.base.replications}
