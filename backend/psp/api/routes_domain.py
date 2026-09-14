"""Domain model: entity types, entities and hierarchies.

The domain model is what problems are stated *about*. Keeping it separate from
the problem model means the same warehouses, crews and vehicles can feed a
routing problem today and a rostering problem tomorrow without being redefined.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from psp.api.deps import SessionDep
from psp.db import models as m

router = APIRouter(prefix="/domain", tags=["domain"])


class EntityTypeIn(BaseModel):
    key: str
    name: str
    description: str | None = None


class EntityIn(BaseModel):
    entity_type: str
    key: str
    name: str
    attributes: dict = Field(default_factory=dict)
    latitude: float | None = None
    longitude: float | None = None


@router.get("/entity-types")
def list_entity_types(session: Session = SessionDep) -> dict:
    rows = session.scalars(select(m.EntityType).order_by(m.EntityType.key)).all()
    counts = {
        row.entity_type_id: 0 for row in session.scalars(select(m.Entity))
    }
    for entity in session.scalars(select(m.Entity)):
        counts[entity.entity_type_id] = counts.get(entity.entity_type_id, 0) + 1
    return {
        "entity_types": [
            {"id": r.id, "key": r.key, "name": r.name, "description": r.description,
             "entities": counts.get(r.id, 0)}
            for r in rows
        ]
    }


@router.post("/entity-types", status_code=status.HTTP_201_CREATED)
def create_entity_type(payload: EntityTypeIn, session: Session = SessionDep) -> dict:
    existing = session.scalar(select(m.EntityType).where(m.EntityType.key == payload.key))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"entity type '{payload.key}' already exists",
        )
    row = m.EntityType(key=payload.key, name=payload.name, description=payload.description)
    session.add(row)
    session.flush()
    return {"id": row.id, "key": row.key}


@router.get("/entities")
def list_entities(entity_type: str | None = None, session: Session = SessionDep) -> dict:
    query = select(m.Entity)
    if entity_type:
        type_row = session.scalar(select(m.EntityType).where(m.EntityType.key == entity_type))
        if type_row is None:
            raise HTTPException(status_code=404, detail=f"no entity type '{entity_type}'")
        query = query.where(m.Entity.entity_type_id == type_row.id)
    rows = session.scalars(query.order_by(m.Entity.key)).all()
    types = {t.id: t.key for t in session.scalars(select(m.EntityType))}
    return {
        "entities": [
            {
                "id": r.id, "key": r.key, "name": r.name,
                "entity_type": types.get(r.entity_type_id),
                "attributes": r.attributes,
                "latitude": r.latitude, "longitude": r.longitude,
            }
            for r in rows
        ]
    }


@router.post("/entities", status_code=status.HTTP_201_CREATED)
def create_entity(payload: EntityIn, session: Session = SessionDep) -> dict:
    type_row = session.scalar(select(m.EntityType).where(m.EntityType.key == payload.entity_type))
    if type_row is None:
        raise HTTPException(
            status_code=404, detail=f"no entity type '{payload.entity_type}'"
        )
    existing = session.scalar(
        select(m.Entity).where(
            m.Entity.entity_type_id == type_row.id, m.Entity.key == payload.key
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"entity '{payload.key}' already exists for type '{payload.entity_type}'",
        )
    row = m.Entity(
        entity_type_id=type_row.id, key=payload.key, name=payload.name,
        attributes=payload.attributes, latitude=payload.latitude, longitude=payload.longitude,
    )
    session.add(row)
    session.flush()
    return {"id": row.id, "key": row.key}


@router.get("/entities/{entity_type}/set")
def entity_set(entity_type: str, session: Session = SessionDep) -> dict:
    """Project an entity type into an index set a problem can be stated over.

    This is the seam between the domain model and the problem model: a problem
    names a set, and the set is populated from real entities rather than typed
    in again.
    """
    type_row = session.scalar(select(m.EntityType).where(m.EntityType.key == entity_type))
    if type_row is None:
        raise HTTPException(status_code=404, detail=f"no entity type '{entity_type}'")
    entities = session.scalars(
        select(m.Entity).where(
            m.Entity.entity_type_id == type_row.id, m.Entity.active.is_(True)
        ).order_by(m.Entity.key)
    ).all()
    return {
        "set": type_row.key,
        "entity_type": type_row.key,
        "elements": [e.key for e in entities],
        "attributes": {e.key: e.attributes for e in entities},
    }
