"""Structure recognition.

A structure hint claims the model has a special shape — currently only
minimum-cost flow. Before a specialised solver is allowed near it, the claim is
checked against the model, and then the concrete network is extracted from the
*compiled* data rather than from the analyst's word.

This is what lets one problem model be solved either by a general MIP engine or
by a network simplex without the analyst choosing, or even knowing.
"""

from __future__ import annotations

from psp.compiler.flat import FlatModel
from psp.ir.model import IRModel
from psp.problem.spec import StructureHint


def verify_structure(model: IRModel, hint: StructureHint) -> tuple[bool, str | None]:
    """Check a hint against the IR without solving anything."""
    if hint.kind != "min_cost_flow":
        return False, f"unknown structure kind '{hint.kind}'"

    required = {
        "arc_variable": hint.arc_variable,
        "cost_parameter": hint.cost_parameter,
        "supply_parameter": hint.supply_parameter,
        "demand_parameter": hint.demand_parameter,
        "tail_set": hint.tail_set,
        "head_set": hint.head_set,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        return False, f"hint is missing {', '.join(missing)}"

    try:
        var = model.var(hint.arc_variable)
        model.param(hint.cost_parameter)
        model.param(hint.supply_parameter)
        model.param(hint.demand_parameter)
        model.set(hint.tail_set)
        model.set(hint.head_set)
        if hint.capacity_parameter:
            model.param(hint.capacity_parameter)
    except KeyError as exc:
        return False, str(exc)

    if var.index_sets != [hint.tail_set, hint.head_set]:
        return False, (
            f"arc variable '{var.name}' is indexed by {var.index_sets}, "
            f"expected ['{hint.tail_set}', '{hint.head_set}']"
        )
    if var.lb != 0:
        return False, "flow variables must have a lower bound of zero"
    return True, None


def annotate_structure(ir: IRModel, flat: FlatModel) -> FlatModel:
    """Materialise the concrete network onto the flat model, if one is claimed.

    Returns the flat model unchanged when there is no verified hint, so callers
    can apply this unconditionally.
    """
    hint = ir.structure
    if not hint or hint.get("kind") != "min_cost_flow":
        return flat

    tail_set = ir.set(hint["tail_set"])
    head_set = ir.set(hint["head_set"])
    cost = ir.param(hint["cost_parameter"])
    supply = ir.param(hint["supply_parameter"])
    demand = ir.param(hint["demand_parameter"])
    capacity = ir.param(hint["capacity_parameter"]) if hint.get("capacity_parameter") else None
    arc_var = hint["arc_variable"]

    # Tail and head sets may overlap (a transshipment node is both), so nodes
    # are namespaced by side to keep the bipartite graph a DAG.
    arcs = []
    for t in tail_set.elements:
        for h in head_set.elements:
            try:
                c = cost.get((t, h))
            except KeyError:
                continue  # an absent cost means the arc does not exist
            arcs.append(
                {
                    "key": f"{arc_var}[{t},{h}]",
                    "tail": f"T:{t}",
                    "head": f"H:{h}",
                    "cost": c,
                    "capacity": (capacity.get((t, h)) if capacity else None),
                }
            )

    nodes: dict[str, float] = {}
    total_supply = 0.0
    for t in tail_set.elements:
        value = supply.get((t,))
        nodes[f"T:{t}"] = value
        total_supply += value
    total_demand = 0.0
    for h in head_set.elements:
        value = demand.get((h,))
        nodes[f"H:{h}"] = -value
        total_demand += value

    # Network simplex needs a balanced network; slack supply is absorbed by a
    # zero-cost dummy sink rather than being reported as infeasible.
    if abs(total_supply - total_demand) > 1e-9:
        if total_supply > total_demand:
            nodes["SINK:slack"] = -(total_supply - total_demand)
            arcs += [
                {"key": f"__slack[{t}]", "tail": f"T:{t}", "head": "SINK:slack",
                 "cost": 0.0, "capacity": None}
                for t in tail_set.elements
            ]
        else:
            # Demand exceeds supply: no feasible flow exists, and saying so here
            # is clearer than letting the engine report an opaque failure.
            flat.structure = {}
            flat.metadata["structure_rejected"] = (
                f"demand ({total_demand:g}) exceeds supply ({total_supply:g}); "
                "the model is infeasible as a pure flow problem"
            )
            return flat

    flat.structure = {
        "kind": "min_cost_flow",
        "arc_variable": arc_var,
        "arcs": arcs,
        "supply": nodes,
    }
    return flat
