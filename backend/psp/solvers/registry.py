"""Solver registry.

Adapters are referenced by name and imported lazily. Lazy import is not an
optimisation here — OR-Tools and highspy each bundle their own copy of HiGHS
and their shared objects clash, so at most one of them may ever be loaded into
a given process. :mod:`psp.execution.runner` keeps them apart by running each
solve in its own worker process; nothing in the API process imports an engine.
"""

from __future__ import annotations

from importlib import import_module

from psp.solvers.base import Capabilities, SolverAdapter

# name -> (module, class, static capability summary for the API/UI)
_REGISTRY: dict[str, tuple[str, str, Capabilities]] = {
    "highs": (
        "psp.solvers.highs_adapter",
        "HighsAdapter",
        Capabilities(continuous=True, integer=True, binary=True, duals=True),
    ),
    "cpsat": (
        "psp.solvers.cpsat_adapter",
        "CpSatAdapter",
        Capabilities(
            continuous=False, integer=True, binary=True, duals=False,
            requires_bounded_integers=True,
        ),
    ),
    "networkx": (
        "psp.solvers.networkx_adapter",
        "NetworkXAdapter",
        Capabilities(
            continuous=True, integer=True, binary=False, duals=False,
            requires_structure="min_cost_flow",
        ),
    ),
}


def available() -> list[str]:
    return sorted(_REGISTRY)


def capabilities(name: str) -> Capabilities:
    """Capability summary without importing the engine."""
    return _REGISTRY[name][2]


def describe() -> list[dict]:
    return [
        {"name": n, "module": m, "capabilities": c.model_dump()}
        for n, (m, _, c) in sorted(_REGISTRY.items())
    ]


def load(name: str) -> SolverAdapter:
    """Import and instantiate an adapter. Only ever called inside a worker."""
    try:
        module_path, class_name, _ = _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown solver '{name}'; available: {', '.join(available())}") from None
    return getattr(import_module(module_path), class_name)()


def select(model, preferred: str | None = None) -> tuple[str, list[dict]]:
    """Pick a solver for a flat model and report why each candidate was kept.

    Selection is capability-driven and deterministic, so the audit trail can
    record not just which engine ran but which engines were eligible.
    """
    order = ["networkx", "cpsat", "highs"]  # most specialised first
    if preferred:
        order = [preferred] + [n for n in order if n != preferred]

    considered: list[dict] = []
    chosen: str | None = None
    for name in order:
        if name not in _REGISTRY:
            considered.append({"solver": name, "eligible": False, "reason": "unknown solver"})
            continue
        ok, reason = _StaticAdapter(name).accepts(model)
        considered.append({"solver": name, "eligible": ok, "reason": reason})
        if ok and chosen is None:
            chosen = name
    if chosen is None:
        raise RuntimeError(
            "no registered solver accepts this model: "
            + "; ".join(f"{c['solver']}: {c['reason']}" for c in considered if c["reason"])
        )
    return chosen, considered


class _StaticAdapter(SolverAdapter):
    """Applies :meth:`SolverAdapter.accepts` from the registry's capability
    summary, so eligibility can be decided without importing any engine."""

    def __init__(self, name: str):
        self.name = name
        self._caps = capabilities(name)

    def capabilities(self) -> Capabilities:
        return self._caps
