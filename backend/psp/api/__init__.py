from fastapi import APIRouter

from psp.api import (
    routes_domain,
    routes_dsl,
    routes_problems,
    routes_runs,
    routes_simulation,
    routes_system,
    routes_templates,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(routes_system.router)
api_router.include_router(routes_templates.router)
api_router.include_router(routes_problems.router)
api_router.include_router(routes_runs.router)
api_router.include_router(routes_domain.router)
api_router.include_router(routes_dsl.router)
api_router.include_router(routes_simulation.router)

__all__ = ["api_router"]
