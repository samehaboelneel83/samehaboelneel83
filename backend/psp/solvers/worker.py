"""Solver worker entry point.

Run as ``python -m psp.solvers.worker <request.json> <result.json>``.

Every solve happens in one of these, never in the API process. Three reasons,
in order of how much they hurt when ignored:

1. OR-Tools and highspy both ship their own build of HiGHS. Loading both into
   one interpreter raises ``ImportError: undefined symbol`` from whichever
   loses the race, so the engines are physically incapable of sharing a
   process.
2. A solver that segfaults or exhausts memory on a pathological instance takes
   its process down with it, not the API.
3. A wall-clock limit enforced from outside is a limit the engine cannot talk
   its way out of.

Request and result travel as files rather than pipes, so an engine writing to
stdout can never corrupt the result payload.
"""

from __future__ import annotations

import json
import sys
import traceback


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: python -m psp.solvers.worker <request.json> <result.json>", file=sys.stderr)
        return 2

    request_path, result_path = argv[1], argv[2]
    with open(request_path) as fh:
        request = json.load(fh)

    try:
        from psp.compiler.flat import FlatModel
        from psp.solvers.base import SolveOptions, SolveResult, SolveStatus
        from psp.solvers.registry import load

        adapter = load(request["solver"])
        model = FlatModel.model_validate(request["model"])
        options = SolveOptions.model_validate(request.get("options") or {})

        accepted, reason = adapter.accepts(model)
        if not accepted:
            result = SolveResult(
                status=SolveStatus.UNSUPPORTED, solver=request["solver"], message=reason
            )
        else:
            result = adapter.solve(model, options)
        payload = result.model_dump(mode="json")
    except Exception as exc:  # engine failures are results, not crashes
        payload = {
            "status": "error",
            "solver": request.get("solver", "unknown"),
            "message": f"{type(exc).__name__}: {exc}",
            "values": {},
            "duals": {},
            "log": traceback.format_exc().splitlines()[-12:],
            "diagnostics": {},
            "wall_time_seconds": 0.0,
        }

    with open(result_path, "w") as fh:
        json.dump(payload, fh)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv))
