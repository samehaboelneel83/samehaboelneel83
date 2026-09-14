"""The provenance chain.

    source -> fact -> parameter -> constraint -> model -> run -> solution -> decision

Every link is recorded as the platform works, not reconstructed afterwards, and
every link is derived from data structures the compiler already produced. That
is the whole point: the chain is a by-product of deterministic compilation, so
the system can say *why* it recommended something without a language model
being asked to guess.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from psp.compiler.pipeline import CompiledProblem
from psp.ir.walk import constraint_parameters, objective_parameters
from psp.provenance.solution import Solution


class ProvenanceNode(BaseModel):
    id: str
    # One of: source, fact, parameter, constraint, objective, model, run,
    # solution, decision.
    kind: str
    label: str
    detail: str | None = None
    attributes: dict = Field(default_factory=dict)


class ProvenanceEdge(BaseModel):
    source: str
    target: str
    relation: str


class ProvenanceGraph(BaseModel):
    nodes: list[ProvenanceNode] = Field(default_factory=list)
    edges: list[ProvenanceEdge] = Field(default_factory=list)

    def add_node(self, node: ProvenanceNode) -> str:
        if not any(n.id == node.id for n in self.nodes):
            self.nodes.append(node)
        return node.id

    def add_edge(self, source: str, target: str, relation: str) -> None:
        edge = ProvenanceEdge(source=source, target=target, relation=relation)
        if edge not in self.edges:
            self.edges.append(edge)


def build_graph(
    compiled: CompiledProblem, solution: Solution, run_id: str
) -> ProvenanceGraph:
    """Build the full chain for one run."""
    g = ProvenanceGraph()
    spec = compiled.spec

    model_id = f"model:{compiled.ir.fingerprint()[:16]}"
    g.add_node(
        ProvenanceNode(
            id=model_id, kind="model", label=compiled.ir.name,
            detail=f"Model IR compiled from problem '{spec.key}'",
            attributes={
                "fingerprint": compiled.ir.fingerprint(),
                "scenario": compiled.record.scenario_key,
                "template": spec.template_key,
                **compiled.flat.stats(),
            },
        )
    )

    # sources and facts -> parameters
    for param in spec.parameters:
        param_id = f"parameter:{param.name}"
        g.add_node(
            ProvenanceNode(
                id=param_id, kind="parameter", label=param.name,
                detail=param.description,
                attributes={"unit": param.unit, "index_sets": param.index_sets,
                            "value_count": len(param.values)},
            )
        )
        g.add_edge(param_id, model_id, "feeds")
        for value in param.values:
            origin = value.origin
            source_name = origin.source if origin else "unattributed"
            source_id = f"source:{source_name}"
            g.add_node(
                ProvenanceNode(
                    id=source_id, kind="source", label=source_name,
                    detail=(origin.note if origin else "no source was recorded for this value"),
                )
            )
            fact_id = f"fact:{param.name}[{','.join(value.index)}]"
            g.add_node(
                ProvenanceNode(
                    id=fact_id, kind="fact",
                    label=f"{param.name}[{', '.join(value.index)}] = {value.value:g}",
                    attributes={"value": value.value, "index": value.index,
                                "confidence": origin.confidence if origin else None},
                )
            )
            g.add_edge(source_id, fact_id, "asserts")
            g.add_edge(fact_id, param_id, "populates")

    # parameters -> constraints and objectives
    for constraint in spec.constraints:
        cid = f"constraint:{constraint.name}"
        g.add_node(
            ProvenanceNode(
                id=cid, kind="constraint", label=constraint.name,
                detail=constraint.statement,
                attributes={"category": constraint.category, "rationale": constraint.rationale},
            )
        )
        g.add_edge(cid, model_id, "restricts")
        for name in constraint_parameters(constraint):
            g.add_edge(f"parameter:{name}", cid, "parameterises")

    for objective in spec.objectives:
        oid = f"objective:{objective.name}"
        g.add_node(
            ProvenanceNode(
                id=oid, kind="objective", label=objective.name,
                detail=objective.statement,
                attributes={"sense": objective.sense, "unit": objective.unit},
            )
        )
        g.add_edge(oid, model_id, "scores")
        for name in objective_parameters(objective):
            g.add_edge(f"parameter:{name}", oid, "parameterises")

    # model -> run -> solution -> decisions
    run_node = f"run:{run_id}"
    g.add_node(
        ProvenanceNode(
            id=run_node, kind="run", label=f"run {run_id}",
            detail=f"solved by {solution.solver}",
            attributes={"solver": solution.solver, "status": solution.status,
                        "wall_time_seconds": solution.wall_time_seconds},
        )
    )
    g.add_edge(model_id, run_node, "executed_as")

    solution_id = f"solution:{run_id}"
    g.add_node(
        ProvenanceNode(
            id=solution_id, kind="solution", label=f"solution {run_id}",
            detail=solution.message,
            attributes={"objectives": [o.model_dump() for o in solution.objectives]},
        )
    )
    g.add_edge(run_node, solution_id, "produced")

    for decision in solution.decisions:
        did = f"decision:{decision.key}"
        g.add_node(
            ProvenanceNode(
                id=did, kind="decision", label=f"{decision.key} = {decision.value:g}",
                detail=decision.meaning, attributes={"value": decision.value},
            )
        )
        g.add_edge(solution_id, did, "recommends")

    # decisions <- the constraints that actually bit
    for outcome in solution.binding_constraints:
        g.add_edge(f"constraint:{outcome.name}", solution_id, "binds")

    return g
