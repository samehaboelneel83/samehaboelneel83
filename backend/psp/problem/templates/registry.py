"""Template registry."""

from __future__ import annotations

from psp.problem.templates.assignment import AssignmentTemplate
from psp.problem.templates.base import ProblemTemplate
from psp.problem.templates.resource_allocation import ResourceAllocationTemplate
from psp.problem.templates.scheduling import SchedulingTemplate
from psp.problem.templates.transportation import TransportationTemplate
from psp.problem.templates.vehicle_routing import VehicleRoutingTemplate

_TEMPLATES: dict[str, ProblemTemplate] = {
    t.key: t
    for t in (
        ResourceAllocationTemplate(),
        AssignmentTemplate(),
        SchedulingTemplate(),
        TransportationTemplate(),
        VehicleRoutingTemplate(),
    )
}


def available() -> list[str]:
    return list(_TEMPLATES)


def get(key: str) -> ProblemTemplate:
    try:
        return _TEMPLATES[key]
    except KeyError:
        raise KeyError(
            f"unknown template '{key}'; available: {', '.join(_TEMPLATES)}"
        ) from None


def describe_all() -> list[dict]:
    return [t.describe() for t in _TEMPLATES.values()]
