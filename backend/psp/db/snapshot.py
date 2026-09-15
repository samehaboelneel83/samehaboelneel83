"""Reading the domain as the binder wants to see it.

The binder takes plain data, not a session, so that a problem can be bound
against a domain written in four lines of a test. This is the one place that
turns the stored domain into that shape.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from psp.db import models as m
from psp.problem.binding import DomainEntity, DomainSnapshot


def read_domain(session: Session) -> DomainSnapshot:
    """Every active entity and relationship, keyed by type."""
    types = {t.id: t.key for t in session.scalars(select(m.EntityType)).all()}
    entities: dict[str, list[DomainEntity]] = {key: [] for key in types.values()}
    by_id: dict[str, str] = {}
    rows = session.scalars(
        select(m.Entity).where(m.Entity.active.is_(True)).order_by(m.Entity.key)
    ).all()
    for row in rows:
        by_id[row.id] = row.key
        entities.setdefault(types.get(row.entity_type_id, "?"), []).append(
            DomainEntity(key=row.key, name=row.name, attributes=dict(row.attributes or {}))
        )

    relationship_types = {
        t.id: t.key for t in session.scalars(select(m.RelationshipType)).all()
    }
    relationships: dict[str, list[tuple[str, str]]] = {
        key: [] for key in relationship_types.values()
    }
    for link in session.scalars(select(m.Relationship)).all():
        kind = relationship_types.get(link.relationship_type_id)
        source, target = by_id.get(link.source_entity_id), by_id.get(link.target_entity_id)
        if kind is None or source is None or target is None:
            continue  # an edge to a retired entity is not an edge
        relationships[kind].append((source, target))
    for edges in relationships.values():
        edges.sort()
    return DomainSnapshot(entities=entities, relationships=relationships)
