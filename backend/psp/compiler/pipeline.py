"""The one function everything else calls: problem -> solver-ready model."""

from __future__ import annotations

from dataclasses import dataclass

from psp.compiler.flat import FlatModel
from psp.compiler.flatten import flatten
from psp.compiler.problem_compiler import CompilationRecord, compile_problem
from psp.compiler.structure import annotate_structure
from psp.ir.model import IRModel
from psp.problem.spec import ProblemSpec


@dataclass
class CompiledProblem:
    spec: ProblemSpec
    ir: IRModel
    flat: FlatModel
    record: CompilationRecord

    @property
    def fingerprint(self) -> str:
        return self.ir.fingerprint()


def compile_and_flatten(spec: ProblemSpec, scenario_key: str | None = None) -> CompiledProblem:
    """Problem Model -> IR -> flat linear system, with the audit record."""
    ir, record = compile_problem(spec, scenario_key)
    flat = annotate_structure(ir, flatten(ir))
    if flat.metadata.get("structure_rejected"):
        record.warnings.append(flat.metadata["structure_rejected"])
    return CompiledProblem(spec=spec, ir=ir, flat=flat, record=record)
