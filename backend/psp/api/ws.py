"""Live solve progress over a WebSocket.

A solve is a multi-stage pipeline and the stages have very different costs —
compiling a large time-indexed model can take as long as solving a small one.
Streaming the stages means the interface can say what is happening rather than
showing an indeterminate spinner, and a failure names the stage it failed in.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from psp.api.deps import build_options
from psp.compiler.errors import CompileError
from psp.compiler.pipeline import compile_and_flatten
from psp.db import models as m
from psp.db.base import SessionLocal
from psp.execution.runner import run_solver
from psp.execution.service import load_spec, persist_model_version, persist_run
from psp.provenance.solution import build_solution

router = APIRouter()


@router.websocket("/ws/solve")
async def solve_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            request = await websocket.receive_json()
            await _run_one(websocket, request)
    except WebSocketDisconnect:
        return
    except Exception as exc:  # never leave the socket open on an unexpected fault
        await websocket.send_json({"stage": "error", "message": f"{type(exc).__name__}: {exc}"})
        await websocket.close()


async def _run_one(websocket: WebSocket, request: dict) -> None:
    key = request.get("problem")
    scenario = request.get("scenario")
    options = build_options(request.get("time_limit_seconds"))

    session = SessionLocal()
    try:
        problem = session.scalar(select(m.Problem).where(m.Problem.key == key))
        if problem is None:
            await websocket.send_json({"stage": "error", "message": f"no problem '{key}'"})
            return

        await websocket.send_json({"stage": "compiling", "problem": key, "scenario": scenario})
        try:
            # The compiler and the solver are both blocking and CPU-bound, so they
            # run in the default executor; keeping them off the event loop is what
            # lets progress messages actually reach the client mid-solve.
            compiled = await asyncio.to_thread(compile_and_flatten, load_spec(problem), scenario)
        except CompileError as exc:
            await websocket.send_json(
                {"stage": "error", "message": str(exc), "where": exc.where}
            )
            return

        await websocket.send_json({
            "stage": "compiled",
            "fingerprint": compiled.ir.fingerprint(),
            "statistics": compiled.flat.stats(),
            "warnings": compiled.record.warnings,
        })

        await websocket.send_json({"stage": "solving", "requested_solver": request.get("solver")})
        result, trace = await asyncio.to_thread(
            run_solver, compiled.flat, request.get("solver"), options
        )
        await websocket.send_json({
            "stage": "solved",
            "solver": trace["solver"],
            "status": result.status.value,
            "wall_time_seconds": result.wall_time_seconds,
        })

        solution = build_solution(compiled, result)
        version = persist_model_version(session, problem, compiled, trace["considered"])
        run, stored = persist_run(
            session, problem, version, compiled, result, solution, trace, options
        )
        session.commit()

        await websocket.send_json({
            "stage": "complete",
            "run_id": run.id,
            "solution_id": stored.id if stored else None,
            "solution": solution.model_dump(mode="json"),
        })
    finally:
        session.close()
