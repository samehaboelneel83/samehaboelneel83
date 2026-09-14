"""NetworkX adapter — structure-recognising engine for network-flow models.

This adapter exists to prove the point of the IR: a transportation problem
compiled once can be solved by a general MIP engine *or*, because the compiler
annotates ``structure``, by a specialised combinatorial algorithm that is orders
of magnitude faster. The problem model never changes. The adapter refuses
anything whose declared structure it does not recognise, so a mis-annotated
model fails loudly instead of being solved as the wrong problem.
"""

from __future__ import annotations

import time

from psp.compiler.flat import FlatModel
from psp.solvers.base import Capabilities, SolveOptions, SolverAdapter, SolveResult, SolveStatus

SCALE = 10**6  # network simplex needs integral costs and capacities


class NetworkXAdapter(SolverAdapter):
    name = "networkx"
    engine = "NetworkX (network simplex)"

    def capabilities(self) -> Capabilities:
        return Capabilities(
            continuous=True, integer=True, binary=False, duals=False,
            requires_structure="min_cost_flow",
        )

    def solve(self, model: FlatModel, options: SolveOptions) -> SolveResult:
        import networkx as nx

        spec = model.structure
        arc_var = spec["arc_variable"]
        # each arc: {"key", "tail", "head", "cost", "capacity"}
        arcs = spec["arcs"]
        supply = spec["supply"]      # {node: net supply (positive) / demand (negative)}

        total = sum(supply.values())
        if abs(total) > 1e-6:
            return SolveResult(
                status=SolveStatus.INFEASIBLE,
                solver=self.name,
                message=(
                    f"supply and demand do not balance (net {total:g}); "
                    "add a dummy source or sink before solving as a flow"
                ),
                diagnostics={"engine": self.engine},
            )

        g = nx.DiGraph()
        for node, value in supply.items():
            g.add_node(node, demand=int(round(-value * SCALE)))
        for arc in arcs:
            capacity = arc.get("capacity")
            g.add_edge(
                arc["tail"], arc["head"],
                weight=int(round(arc["cost"] * SCALE)),
                capacity=(int(round(capacity * SCALE)) if capacity is not None else 2**53),
                key=arc["key"],
            )

        started = time.perf_counter()
        try:
            cost, flow = nx.network_simplex(g)
        except nx.NetworkXUnfeasible as exc:
            return SolveResult(
                status=SolveStatus.INFEASIBLE, solver=self.name, message=str(exc),
                wall_time_seconds=time.perf_counter() - started,
                diagnostics={"engine": self.engine},
            )
        elapsed = time.perf_counter() - started

        values = {v.key: 0.0 for v in model.variables if v.name == arc_var}
        for tail, heads in flow.items():
            for head, amount in heads.items():
                key = g.edges[tail, head]["key"]
                if key in values:
                    values[key] = amount / SCALE

        return SolveResult(
            status=SolveStatus.OPTIMAL,
            solver=self.name,
            objective_value=cost / (SCALE * SCALE) + model.objective.constant,
            values=values,
            wall_time_seconds=elapsed,
            message="network simplex optimal",
            diagnostics={
                "engine": self.engine,
                "nodes": g.number_of_nodes(),
                "arcs": g.number_of_edges(),
                **model.stats(),
            },
        )
