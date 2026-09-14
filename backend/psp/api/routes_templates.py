"""Problem template catalogue."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from psp.problem.templates.registry import describe_all, get

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("")
def list_templates() -> dict:
    return {"templates": describe_all()}


@router.get("/{key}")
def get_template(key: str) -> dict:
    try:
        template = get(key)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {**template.describe(), "example": template.example()}


@router.get("/{key}/example")
def get_example(key: str) -> dict:
    try:
        template = get(key)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"template": key, "data": template.example()}
