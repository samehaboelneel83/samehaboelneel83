"""Problem Model -> Model IR.

This pass is deliberately dull. It applies a scenario, validates the problem
against itself, and copies each element into the IR while recording a mapping
row that says which problem element produced which IR element and where its
data came from. Dullness is the point: if the compiler were clever, nobody
could explain its output.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from psp.compiler.errors import CompileError
from psp.compiler.structure import verify_structure
from psp.ir.model import IRConstraint, IRModel, IRObjective, IRParam, IRSet, IRVar
from psp.problem.spec import ProblemSpec, Scenario


@dataclass
class Mapping:
    """One IR element and the problem element it came from."""

    ir_kind: str
    ir_name: str
    problem_kind: str
    problem_name: str
    statement: str | None = None
    origin: dict | None = None


@dataclass
class CompilationRecord:
    """Everything an auditor needs to know about one compilation."""

    problem_key: str
    scenario_key: str | None
    mappings: list[Mapping] = field(default_factory=list)
    applied_overrides: list[dict] = field(default_factory=list)
    assumptions: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "problem_key": self.problem_key,
            "scenario_key": self.scenario_key,
            "mappings": [m.__dict__ for m in self.mappings],
            "applied_overrides": self.applied_overrides,
            "assumptions": self.assumptions,
            "warnings": self.warnings,
        }


def compile_problem(
    spec: ProblemSpec, scenario_key: str | None = None
) -> tuple[IRModel, CompilationRecord]:
    """Compile a problem (under an optional scenario) into the Model IR."""
    record = CompilationRecord(problem_key=spec.key, scenario_key=scenario_key)
    scenario = spec.scenario(scenario_key) if scenario_key else None

    _validate(spec)

    sets = [
        IRSet(name=s.name, kind=s.kind, elements=list(s.elements), description=s.description)
        for s in spec.sets
    ]
    for s, ir_set in zip(spec.sets, sets, strict=True):
        record.mappings.append(
            Mapping("set", ir_set.name, "problem_set", s.name,
                    statement=s.description,
                    origin={"entity_type": s.entity_type} if s.entity_type else None)
        )

    parameters = [_compile_parameter(p, scenario, record) for p in spec.parameters]

    variables = [
        IRVar(name=v.name, index_sets=v.index_sets, kind=v.kind, lb=v.lb, ub=v.ub,
              description=v.description)
        for v in spec.variables
    ]
    for v in spec.variables:
        record.mappings.append(
            Mapping("variable", v.name, "decision_variable", v.name,
                    statement=v.decision_meaning or v.description)
        )

    constraints = []
    for c in spec.constraints:
        constraints.append(
            IRConstraint(name=c.name, forall=c.forall, where=c.where, rel=c.rel,
                         statement=c.statement, origin=c.category)
        )
        record.mappings.append(
            Mapping("constraint", c.name, "constraint", c.name, statement=c.statement,
                    origin=(c.origin.model_dump() if c.origin else {"category": c.category}))
        )

    objectives = []
    for o in spec.objectives:
        objectives.append(
            IRObjective(name=o.name, sense=o.sense, expr=o.expr, weight=o.weight, unit=o.unit)
        )
        record.mappings.append(
            Mapping("objective", o.name, "objective", o.name, statement=o.statement)
        )

    record.assumptions = [a.model_dump() for a in spec.assumptions]

    model = IRModel(
        name=f"{spec.key}" + (f"@{scenario_key}" if scenario_key else ""),
        sets=sets,
        parameters=parameters,
        variables=variables,
        constraints=constraints,
        objectives=objectives,
        metadata={
            "problem_key": spec.key,
            "problem_name": spec.name,
            "problem_type": spec.problem_type,
            "scenario": scenario_key,
            "template": spec.template_key,
        },
    )

    if spec.structure is not None:
        ok, reason = verify_structure(model, spec.structure)
        if ok:
            model.structure = spec.structure.model_dump(exclude_none=True)
        else:
            # A hint that cannot be verified is dropped, never trusted. The model
            # still solves; it just uses a general engine.
            record.warnings.append(f"structure hint '{spec.structure.kind}' not applied: {reason}")

    return model, record


def _compile_parameter(param, scenario: Scenario | None, record: CompilationRecord) -> IRParam:
    values: dict[str, float] = {}
    origins: dict[str, dict] = {}
    for pv in param.values:
        key = IRParam.key(pv.index)
        values[key] = pv.value
        if pv.origin is not None:
            origins[key] = pv.origin.model_dump(exclude_none=True)

    if scenario is not None:
        for ov in scenario.overrides:
            if ov.parameter != param.name:
                continue
            key = IRParam.key(ov.index)
            before = values.get(key, param.default)
            if ov.value is not None:
                after = ov.value
            elif ov.scale is not None:
                if before is None:
                    raise CompileError(
                        f"scenario '{scenario.key}' scales '{param.name}[{','.join(ov.index)}]' "
                        "but it has no baseline value"
                    )
                after = before * ov.scale
            else:
                continue
            values[key] = after
            record.applied_overrides.append(
                {
                    "scenario": scenario.key,
                    "parameter": param.name,
                    "index": ov.index,
                    "baseline": before,
                    "value": after,
                }
            )
            origins[key] = {"source": f"scenario:{scenario.key}", "note": "scenario override"}

    record.mappings.append(
        Mapping("parameter", param.name, "parameter", param.name,
                statement=param.description, origin={"values": origins} if origins else None)
    )
    return IRParam(
        name=param.name,
        index_sets=param.index_sets,
        values=values,
        default=param.default,
        unit=param.unit,
        description=param.description,
    )


def _validate(spec: ProblemSpec) -> None:
    """Catch the errors that are cheap to catch here and awful to debug later."""
    set_names = {s.name for s in spec.sets}
    _no_duplicates([s.name for s in spec.sets], "set")
    _no_duplicates([p.name for p in spec.parameters], "parameter")
    _no_duplicates([v.name for v in spec.variables], "variable")
    _no_duplicates([c.name for c in spec.constraints], "constraint")
    _no_duplicates([o.name for o in spec.objectives], "objective")

    for p in spec.parameters:
        for s in p.index_sets:
            if s not in set_names:
                raise CompileError(f"parameter '{p.name}' indexed by unknown set '{s}'")
        for pv in p.values:
            if len(pv.index) != len(p.index_sets):
                raise CompileError(
                    f"parameter '{p.name}' expects {len(p.index_sets)} subscript(s); "
                    f"value [{', '.join(pv.index)}] has {len(pv.index)}"
                )
    for v in spec.variables:
        for s in v.index_sets:
            if s not in set_names:
                raise CompileError(f"variable '{v.name}' indexed by unknown set '{s}'")
    for c in spec.constraints:
        for b in c.forall:
            if b.set not in set_names:
                raise CompileError(f"constraint '{c.name}' quantified over unknown set '{b.set}'")
    if not spec.variables:
        raise CompileError(f"problem '{spec.key}' declares no decision variables")


def _no_duplicates(names: list[str], kind: str) -> None:
    seen: set[str] = set()
    for n in names:
        if n in seen:
            raise CompileError(f"duplicate {kind} name '{n}'")
        seen.add(n)
