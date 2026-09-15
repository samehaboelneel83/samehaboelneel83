"""Authoring problems as text.

Three endpoints, which together are the whole authoring loop: check what you
have written, save it, and read an existing problem back out as source so it
can be edited — or so a built-in template can be used as a worked example.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from psp.api.deps import ProblemDep, SessionDep
from psp.compiler.errors import CompileError
from psp.compiler.pipeline import compile_and_flatten
from psp.db import models as m
from psp.dsl import DslError, parse_problem, write_problem
from psp.db.snapshot import read_domain
from psp.execution.service import load_spec, persist_problem
from psp.problem.binding import BindingError, bind, needs_binding
from psp.problem.templates.registry import get as get_template

router = APIRouter(prefix="/dsl", tags=["authoring"])


class SourceRequest(BaseModel):
    source: str
    save: bool = False


def _bind(spec, session: Session):
    """Fill in whatever the problem asked the domain for.

    Bound once, here, and what is stored is the result. A problem that resolved
    its sets at solve time would answer a different question every time the
    organisation hired someone, with nothing in the run record to say so.
    """
    if not needs_binding(spec):
        return spec
    try:
        return bind(spec, read_domain(session))
    except BindingError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"kind": "binding", "message": str(exc), "known": exc.known},
        ) from exc


def _parse(source: str):
    try:
        return parse_problem(source)
    except DslError as exc:
        # A language error is the author's to fix, and useless without its
        # location, so the whole diagnostic travels rather than just a string.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"kind": "dsl", **exc.to_dict()},
        ) from exc


@router.post("/check")
def check(request: SourceRequest, session: Session = SessionDep) -> dict:
    """Parse and compile without solving, so an author can see what they built."""
    spec = _bind(_parse(request.source), session)
    try:
        compiled = compile_and_flatten(spec)
    except CompileError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"kind": "compile", "message": str(exc), "where": exc.where},
        ) from exc
    return {
        "key": spec.key,
        "name": spec.name,
        "statistics": compiled.flat.stats(),
        "fingerprint": compiled.ir.fingerprint(),
        "structure": compiled.flat.structure.get("kind"),
        "warnings": compiled.record.warnings,
        "summary": {
            "sets": [{"name": s.name, "elements": len(s.elements), "kind": s.kind}
                     for s in spec.sets],
            "parameters": [p.name for p in spec.parameters],
            "variables": [{"name": v.name, "kind": v.kind} for v in spec.variables],
            "constraints": [{"name": c.name, "statement": c.statement} for c in spec.constraints],
            "objectives": [{"name": o.name, "sense": o.sense} for o in spec.objectives],
            "assumptions": len(spec.assumptions),
            "scenarios": [s.key for s in spec.scenarios],
        },
    }


@router.post("/problems", status_code=status.HTTP_201_CREATED)
def create(request: SourceRequest, session: Session = SessionDep) -> dict:
    """Save a problem written as text."""
    spec = _bind(_parse(request.source), session)
    try:
        compile_and_flatten(spec)
    except CompileError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"kind": "compile", "message": str(exc), "where": exc.where},
        ) from exc
    spec.metadata = {**spec.metadata, "source": request.source}
    problem = persist_problem(session, spec)
    return {"key": problem.key, "id": problem.id, "saved": True}


@router.get("/problems/{key}")
def export(problem: m.Problem = ProblemDep) -> dict:
    """Read a stored problem back as source text."""
    return {"key": problem.key, "source": write_problem(load_spec(problem))}


@router.get("/templates/{key}")
def example(key: str) -> dict:
    """A built-in template rendered as source — the language by example."""
    try:
        template = get_template(key)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {
        "template": key,
        "title": template.title,
        "source": write_problem(template.build(template.example())),
    }
