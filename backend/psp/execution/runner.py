"""Runs a flat model in an isolated solver process."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from psp.compiler.flat import FlatModel
from psp.solvers.base import SolveOptions, SolveResult, SolveStatus
from psp.solvers.registry import select

GRACE_SECONDS = 15.0


def run_solver(
    model: FlatModel,
    solver: str | None = None,
    options: SolveOptions | None = None,
) -> tuple[SolveResult, dict]:
    """Solve ``model`` out-of-process. Returns the result and a selection trace.

    The trace records every engine that was considered and why it was or was
    not eligible, so an audit can reconstruct the choice without re-running it.
    """
    options = options or SolveOptions()
    chosen, considered = select(model, preferred=solver)

    with tempfile.TemporaryDirectory(prefix="psp-solve-") as tmp:
        request_path = Path(tmp) / "request.json"
        result_path = Path(tmp) / "result.json"
        request_path.write_text(
            json.dumps(
                {
                    "solver": chosen,
                    "model": model.model_dump(mode="json"),
                    "options": options.model_dump(mode="json"),
                }
            )
        )

        env = dict(os.environ)
        # The repository root must be importable inside the worker.
        env["PYTHONPATH"] = os.pathsep.join(
            [str(Path(__file__).resolve().parents[2]), env.get("PYTHONPATH", "")]
        ).strip(os.pathsep)

        started = time.perf_counter()
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "psp.solvers.worker", str(request_path), str(result_path)],
                capture_output=True,
                text=True,
                timeout=options.time_limit_seconds + GRACE_SECONDS,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return (
                SolveResult(
                    status=SolveStatus.TIMEOUT,
                    solver=chosen,
                    wall_time_seconds=time.perf_counter() - started,
                    message=(
                        f"solver process exceeded its hard limit of "
                        f"{options.time_limit_seconds + GRACE_SECONDS:g}s and was terminated"
                    ),
                ),
                {"solver": chosen, "considered": considered, "killed": True},
            )
        elapsed = time.perf_counter() - started

        if not result_path.exists():
            return (
                SolveResult(
                    status=SolveStatus.ERROR,
                    solver=chosen,
                    wall_time_seconds=elapsed,
                    message=f"solver process exited with code {completed.returncode}",
                    log=(completed.stderr or "").splitlines()[-20:],
                ),
                {"solver": chosen, "considered": considered, "returncode": completed.returncode},
            )

        result = SolveResult.model_validate(json.loads(result_path.read_text()))

    result.diagnostics.setdefault("process_wall_time_seconds", elapsed)
    trace = {
        "solver": chosen,
        "considered": considered,
        "requested": solver,
        "process_wall_time_seconds": elapsed,
    }
    return result, trace
