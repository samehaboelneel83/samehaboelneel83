"""The persistent schema.

Organised into the layers the platform reasons in — IAM, DOMAIN, PROBLEM,
MODEL, SOLVER, EXECUTION, SOLUTION, PROVENANCE — because the schema is the part
of the system that outlives every framework choice above it.

Two decisions are worth stating plainly:

* **Model versioning is first-class.** A model version is immutable and carries
  the fingerprint of the IR it was compiled from. A run points at a version,
  never at a mutable problem, so a stored solution can always be reproduced.
* **Provenance is first-class.** Sources, facts, evidence and audit events are
  tables, not log lines, because "why did the system recommend this?" has to be
  answerable months later by someone who was not there.

Flexible attributes use a portable JSON column so the ORM runs on both
PostgreSQL and SQLite. The PostgreSQL migration upgrades those columns to JSONB
and adds ltree hierarchies, PostGIS geometry and partitioning — see
``migrations/sql``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from psp.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


# --------------------------------------------------------------------- IAM


class Organization(Timestamped, Base):
    __tablename__ = "organization"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("organization.id"))
    key: Mapped[str] = mapped_column(String(128), unique=True)
    name: Mapped[str] = mapped_column(String(256))
    # Materialised path. PostgreSQL upgrades this to ltree, which gives
    # ancestor/descendant queries an index; the closure table below keeps the
    # same questions answerable on any engine.
    path: Mapped[str] = mapped_column(String(1024), default="")
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)


class OrganizationClosure(Base):
    """Ancestor/descendant pairs, so hierarchy queries never recurse in Python."""

    __tablename__ = "organization_closure"

    ancestor_id: Mapped[str] = mapped_column(ForeignKey("organization.id"), primary_key=True)
    descendant_id: Mapped[str] = mapped_column(ForeignKey("organization.id"), primary_key=True)
    depth: Mapped[int] = mapped_column(Integer)


class User(Timestamped, Base):
    __tablename__ = "app_user"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organization.id"))
    # The identity provider owns credentials; this row only mirrors the subject
    # claim so rows elsewhere can be attributed to a person.
    subject: Mapped[str] = mapped_column(String(256), unique=True)
    username: Mapped[str] = mapped_column(String(256))
    email: Mapped[str | None] = mapped_column(String(320))
    display_name: Mapped[str | None] = mapped_column(String(256))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Role(Base):
    __tablename__ = "role"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(String(128), unique=True)
    name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text)


class UserRole(Base):
    __tablename__ = "user_role"

    user_id: Mapped[str] = mapped_column(ForeignKey("app_user.id"), primary_key=True)
    role_id: Mapped[str] = mapped_column(ForeignKey("role.id"), primary_key=True)
    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("organization.id"), primary_key=True, default=""
    )
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ------------------------------------------------------------------ DOMAIN


class EntityType(Timestamped, Base):
    """What kinds of thing exist in this organisation's world."""

    __tablename__ = "entity_type"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(String(128), unique=True)
    name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text)
    schema_: Mapped[dict] = mapped_column("schema", JSON, default=dict)


class Entity(Timestamped, Base):
    __tablename__ = "entity"
    __table_args__ = (
        UniqueConstraint("entity_type_id", "key", name="uq_entity_type_key"),
        Index("ix_entity_type", "entity_type_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    entity_type_id: Mapped[str] = mapped_column(ForeignKey("entity_type.id"))
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organization.id"))
    key: Mapped[str] = mapped_column(String(256))
    name: Mapped[str] = mapped_column(String(512))
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    # PostGIS upgrades this to geography(Point, 4326) in the PostgreSQL migration.
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    entity_type: Mapped[EntityType] = relationship()


class AttributeDefinition(Base):
    """Declares which attributes an entity type may carry, and their units.

    JSON gives flexibility; this table gives the flexibility a contract, so a
    parameter drawn from an attribute has a known unit and type.
    """

    __tablename__ = "attribute_definition"
    __table_args__ = (UniqueConstraint("entity_type_id", "key", name="uq_attrdef_type_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    entity_type_id: Mapped[str] = mapped_column(ForeignKey("entity_type.id"))
    key: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(256))
    data_type: Mapped[str] = mapped_column(String(32), default="number")
    unit: Mapped[str | None] = mapped_column(String(64))
    required: Mapped[bool] = mapped_column(Boolean, default=False)


class EntityAttribute(Base):
    """A typed attribute value, kept alongside the JSON blob when it needs
    provenance or history of its own."""

    __tablename__ = "entity_attribute"
    __table_args__ = (
        UniqueConstraint("entity_id", "attribute_definition_id", "valid_from",
                         name="uq_entity_attr_valid"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    entity_id: Mapped[str] = mapped_column(ForeignKey("entity.id"))
    attribute_definition_id: Mapped[str] = mapped_column(ForeignKey("attribute_definition.id"))
    numeric_value: Mapped[float | None] = mapped_column(Float)
    text_value: Mapped[str | None] = mapped_column(Text)
    json_value: Mapped[dict | None] = mapped_column(JSON)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_id: Mapped[str | None] = mapped_column(ForeignKey("source.id"))


class RelationshipType(Base):
    __tablename__ = "relationship_type"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(String(128), unique=True)
    name: Mapped[str] = mapped_column(String(256))
    directed: Mapped[bool] = mapped_column(Boolean, default=True)


class Relationship(Base):
    __tablename__ = "relationship"
    __table_args__ = (Index("ix_relationship_source", "source_entity_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    relationship_type_id: Mapped[str] = mapped_column(ForeignKey("relationship_type.id"))
    source_entity_id: Mapped[str] = mapped_column(ForeignKey("entity.id"))
    target_entity_id: Mapped[str] = mapped_column(ForeignKey("entity.id"))
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)


class Hierarchy(Base):
    __tablename__ = "hierarchy"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(String(128), unique=True)
    name: Mapped[str] = mapped_column(String(256))
    entity_type_id: Mapped[str | None] = mapped_column(ForeignKey("entity_type.id"))


class HierarchyNode(Base):
    __tablename__ = "hierarchy_node"
    __table_args__ = (Index("ix_hierarchy_node_path", "hierarchy_id", "path"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    hierarchy_id: Mapped[str] = mapped_column(ForeignKey("hierarchy.id"))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("hierarchy_node.id"))
    entity_id: Mapped[str | None] = mapped_column(ForeignKey("entity.id"))
    path: Mapped[str] = mapped_column(String(1024), default="")
    depth: Mapped[int] = mapped_column(Integer, default=0)


class RoleType(Base):
    """A role an entity can play, as distinct from what it is."""

    __tablename__ = "role_type"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(String(128), unique=True)
    name: Mapped[str] = mapped_column(String(256))


class EntityRole(Base):
    __tablename__ = "entity_role"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    entity_id: Mapped[str] = mapped_column(ForeignKey("entity.id"))
    role_type_id: Mapped[str] = mapped_column(ForeignKey("role_type.id"))
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StateType(Base):
    __tablename__ = "state_type"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(String(128), unique=True)
    name: Mapped[str] = mapped_column(String(256))
    entity_type_id: Mapped[str | None] = mapped_column(ForeignKey("entity_type.id"))


class EntityState(Base):
    __tablename__ = "entity_state"
    __table_args__ = (Index("ix_entity_state_entity", "entity_id", "valid_from"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    entity_id: Mapped[str] = mapped_column(ForeignKey("entity.id"))
    state_type_id: Mapped[str] = mapped_column(ForeignKey("state_type.id"))
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)


class EventType(Base):
    __tablename__ = "event_type"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(String(128), unique=True)
    name: Mapped[str] = mapped_column(String(256))


class Event(Base):
    """Something that happened. Partitioned by time in PostgreSQL."""

    __tablename__ = "event"
    __table_args__ = (Index("ix_event_occurred", "occurred_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_type_id: Mapped[str] = mapped_column(ForeignKey("event_type.id"))
    entity_id: Mapped[str | None] = mapped_column(ForeignKey("entity.id"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


# ----------------------------------------------------------------- PROBLEM


class ProblemType(Base):
    __tablename__ = "problem_type"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(String(128), unique=True)
    name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text)


class Problem(Timestamped, Base):
    """A stated problem.

    The authoritative Problem Model lives in ``spec`` as the JSON form of
    :class:`psp.problem.spec.ProblemSpec`. The normalised child tables below
    exist so the same content is queryable, reportable and referenceable by
    provenance rows without parsing JSON.
    """

    __tablename__ = "problem"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organization.id"))
    problem_type_id: Mapped[str | None] = mapped_column(ForeignKey("problem_type.id"))
    key: Mapped[str] = mapped_column(String(128), unique=True)
    name: Mapped[str] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text)
    template_key: Mapped[str | None] = mapped_column(String(128))
    spec: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    created_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))


class Parameter(Base):
    __tablename__ = "parameter"
    __table_args__ = (UniqueConstraint("problem_id", "name", name="uq_parameter_problem_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    problem_id: Mapped[str] = mapped_column(ForeignKey("problem.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(128))
    index_sets: Mapped[list] = mapped_column(JSON, default=list)
    unit: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)
    values: Mapped[list] = mapped_column(JSON, default=list)


class Variable(Base):
    __tablename__ = "variable"
    __table_args__ = (UniqueConstraint("problem_id", "name", name="uq_variable_problem_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    problem_id: Mapped[str] = mapped_column(ForeignKey("problem.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(128))
    index_sets: Mapped[list] = mapped_column(JSON, default=list)
    kind: Mapped[str] = mapped_column(String(32), default="continuous")
    lower_bound: Mapped[float] = mapped_column(Float, default=0.0)
    upper_bound: Mapped[float | None] = mapped_column(Float)
    decision_meaning: Mapped[str | None] = mapped_column(Text)


class Constraint(Base):
    __tablename__ = "constraint_"
    __table_args__ = (UniqueConstraint("problem_id", "name", name="uq_constraint_problem_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    problem_id: Mapped[str] = mapped_column(ForeignKey("problem.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(128))
    statement: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(32), default="operational")
    rationale: Mapped[str | None] = mapped_column(Text)
    expression: Mapped[dict] = mapped_column(JSON, default=dict)


class Objective(Base):
    __tablename__ = "objective"
    __table_args__ = (UniqueConstraint("problem_id", "name", name="uq_objective_problem_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    problem_id: Mapped[str] = mapped_column(ForeignKey("problem.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(128))
    statement: Mapped[str] = mapped_column(Text)
    sense: Mapped[str] = mapped_column(String(16))
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    unit: Mapped[str | None] = mapped_column(String(64))
    expression: Mapped[dict] = mapped_column(JSON, default=dict)


class Rule(Base):
    """A business rule not yet expressed as a constraint.

    Rules are kept distinct from constraints on purpose: a rule is a policy
    statement owned by the business, a constraint is its encoding. Keeping both
    means the encoding can be reviewed against the policy it claims to enforce.
    """

    __tablename__ = "rule"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    problem_id: Mapped[str] = mapped_column(ForeignKey("problem.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(128))
    statement: Mapped[str] = mapped_column(Text)
    enforced_by: Mapped[str | None] = mapped_column(String(128))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Assumption(Base):
    __tablename__ = "assumption"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    problem_id: Mapped[str] = mapped_column(ForeignKey("problem.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(128))
    statement: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)
    affects: Mapped[list] = mapped_column(JSON, default=list)


class Scenario(Base):
    __tablename__ = "scenario"
    __table_args__ = (UniqueConstraint("problem_id", "key", name="uq_scenario_problem_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    problem_id: Mapped[str] = mapped_column(ForeignKey("problem.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text)
    overrides: Mapped[list] = mapped_column(JSON, default=list)


class Uncertainty(Base):
    __tablename__ = "uncertainty"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    problem_id: Mapped[str] = mapped_column(ForeignKey("problem.id", ondelete="CASCADE"))
    parameter_name: Mapped[str] = mapped_column(String(128))
    index: Mapped[list] = mapped_column(JSON, default=list)
    distribution: Mapped[str] = mapped_column(String(32), default="uniform")
    relative: Mapped[bool] = mapped_column(Boolean, default=True)
    low: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    mode: Mapped[float | None] = mapped_column(Float)


# ------------------------------------------------------------------- MODEL


class ModelTemplate(Base):
    __tablename__ = "model_template"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(String(128), unique=True)
    name: Mapped[str] = mapped_column(String(256))
    category: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[str | None] = mapped_column(Text)
    inputs: Mapped[list] = mapped_column(JSON, default=list)


class ComputationalModel(Timestamped, Base):
    __tablename__ = "computational_model"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    problem_id: Mapped[str] = mapped_column(ForeignKey("problem.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text)


class ModelVersion(Base):
    """An immutable compiled model. Never updated in place."""

    __tablename__ = "model_version"
    __table_args__ = (
        UniqueConstraint("computational_model_id", "version", name="uq_model_version"),
        Index("ix_model_version_fingerprint", "fingerprint"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    computational_model_id: Mapped[str] = mapped_column(
        ForeignKey("computational_model.id", ondelete="CASCADE")
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    scenario_key: Mapped[str | None] = mapped_column(String(128))
    fingerprint: Mapped[str] = mapped_column(String(64))
    ir: Mapped[dict] = mapped_column(JSON, default=dict)
    compilation_record: Mapped[dict] = mapped_column(JSON, default=dict)
    statistics: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ModelVariable(Base):
    __tablename__ = "model_variable"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    model_version_id: Mapped[str] = mapped_column(
        ForeignKey("model_version.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(128))
    index_sets: Mapped[list] = mapped_column(JSON, default=list)
    kind: Mapped[str] = mapped_column(String(32))
    column_count: Mapped[int] = mapped_column(Integer, default=0)


class ModelConstraint(Base):
    __tablename__ = "model_constraint"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    model_version_id: Mapped[str] = mapped_column(
        ForeignKey("model_version.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(128))
    statement: Mapped[str | None] = mapped_column(Text)
    row_count: Mapped[int] = mapped_column(Integer, default=0)


class ModelObjective(Base):
    __tablename__ = "model_objective"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    model_version_id: Mapped[str] = mapped_column(
        ForeignKey("model_version.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(128))
    sense: Mapped[str] = mapped_column(String(16))
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    unit: Mapped[str | None] = mapped_column(String(64))


class ModelMapping(Base):
    """One row per IR element, naming the problem element it came from.

    This is the join that makes an explanation possible: from a solver row back
    to a sentence a person wrote.
    """

    __tablename__ = "model_mapping"
    __table_args__ = (Index("ix_model_mapping_version", "model_version_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    model_version_id: Mapped[str] = mapped_column(
        ForeignKey("model_version.id", ondelete="CASCADE")
    )
    ir_kind: Mapped[str] = mapped_column(String(32))
    ir_name: Mapped[str] = mapped_column(String(256))
    problem_kind: Mapped[str] = mapped_column(String(32))
    problem_name: Mapped[str] = mapped_column(String(256))
    statement: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[dict | None] = mapped_column(JSON)


class ModelArtifact(Base):
    """A serialised form of the model — flat model, LP file, engine input."""

    __tablename__ = "model_artifact"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    model_version_id: Mapped[str] = mapped_column(
        ForeignKey("model_version.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(32))
    content_type: Mapped[str] = mapped_column(String(64), default="application/json")
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    byte_size: Mapped[int] = mapped_column(Integer, default=0)


# ------------------------------------------------------------------ SOLVER


class Solver(Base):
    __tablename__ = "solver"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    engine: Mapped[str] = mapped_column(String(128))
    version: Mapped[str | None] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class SolverCapability(Base):
    __tablename__ = "solver_capability"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    solver_id: Mapped[str] = mapped_column(ForeignKey("solver.id", ondelete="CASCADE"))
    capability: Mapped[str] = mapped_column(String(64))
    supported: Mapped[bool] = mapped_column(Boolean, default=True)
    detail: Mapped[str | None] = mapped_column(Text)


class ModelSolver(Base):
    """Which solvers were eligible for a model version, and why."""

    __tablename__ = "model_solver"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    model_version_id: Mapped[str] = mapped_column(
        ForeignKey("model_version.id", ondelete="CASCADE")
    )
    solver_key: Mapped[str] = mapped_column(String(64))
    eligible: Mapped[bool] = mapped_column(Boolean, default=True)
    reason: Mapped[str | None] = mapped_column(Text)


# --------------------------------------------------------------- EXECUTION


class Run(Timestamped, Base):
    __tablename__ = "run"
    __table_args__ = (Index("ix_run_model_version", "model_version_id", "started_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    model_version_id: Mapped[str] = mapped_column(ForeignKey("model_version.id"))
    problem_id: Mapped[str] = mapped_column(ForeignKey("problem.id"))
    scenario_key: Mapped[str | None] = mapped_column(String(128))
    solver_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    wall_time_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str | None] = mapped_column(Text)
    selection_trace: Mapped[dict] = mapped_column(JSON, default=dict)
    requested_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))


class RunParameter(Base):
    """The solver options a run actually used, so it can be repeated exactly."""

    __tablename__ = "run_parameter"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(128))
    value: Mapped[str] = mapped_column(Text)


class RunLog(Base):
    """Partitioned by time in PostgreSQL; logs outgrow everything else."""

    __tablename__ = "run_log"
    __table_args__ = (Index("ix_run_log_run", "run_id", "sequence"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"))
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    level: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(Text)
    logged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RunMetric(Base):
    __tablename__ = "run_metric"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(128))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(64))


# ---------------------------------------------------------------- SOLUTION


class Solution(Base):
    __tablename__ = "solution"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(32))
    objective_values: Mapped[list] = mapped_column(JSON, default=list)
    gap: Mapped[float | None] = mapped_column(Float)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SolutionVariable(Base):
    __tablename__ = "solution_variable"
    __table_args__ = (Index("ix_solution_variable_solution", "solution_id", "variable_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    solution_id: Mapped[str] = mapped_column(ForeignKey("solution.id", ondelete="CASCADE"))
    variable_name: Mapped[str] = mapped_column(String(128))
    index: Mapped[list] = mapped_column(JSON, default=list)
    key: Mapped[str] = mapped_column(String(512))
    value: Mapped[float] = mapped_column(Float)


class Decision(Base):
    """A solution variable promoted to something a person will act on."""

    __tablename__ = "decision"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    solution_id: Mapped[str] = mapped_column(ForeignKey("solution.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text)
    value: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), default="proposed")
    decided_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Recommendation(Base):
    __tablename__ = "recommendation"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    solution_id: Mapped[str] = mapped_column(ForeignKey("solution.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(512))
    detail: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    evidence: Mapped[list] = mapped_column(JSON, default=list)


class SolutionComparison(Base):
    __tablename__ = "solution_comparison"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    problem_id: Mapped[str] = mapped_column(ForeignKey("problem.id", ondelete="CASCADE"))
    baseline_solution_id: Mapped[str] = mapped_column(ForeignKey("solution.id"))
    comparison_solution_id: Mapped[str] = mapped_column(ForeignKey("solution.id"))
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SensitivityAnalysis(Base):
    __tablename__ = "sensitivity_analysis"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    problem_id: Mapped[str] = mapped_column(ForeignKey("problem.id", ondelete="CASCADE"))
    base_run_id: Mapped[str | None] = mapped_column(ForeignKey("run.id"))
    method: Mapped[str] = mapped_column(String(64), default="one_at_a_time")
    results: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# -------------------------------------------------------------- PROVENANCE


class Source(Base):
    """Where information entered the platform."""

    __tablename__ = "source"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(String(256), unique=True)
    name: Mapped[str] = mapped_column(String(512))
    kind: Mapped[str] = mapped_column(String(64), default="user_input")
    uri: Mapped[str | None] = mapped_column(String(1024))
    trust_level: Mapped[str | None] = mapped_column(String(32))


class SourceDocument(Base):
    __tablename__ = "source_document"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source_id: Mapped[str] = mapped_column(ForeignKey("source.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(512))
    uri: Mapped[str | None] = mapped_column(String(1024))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SourceFragment(Base):
    """The specific part of a document an assertion rests on."""

    __tablename__ = "source_fragment"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source_document_id: Mapped[str] = mapped_column(
        ForeignKey("source_document.id", ondelete="CASCADE")
    )
    locator: Mapped[str] = mapped_column(String(256))
    excerpt: Mapped[str | None] = mapped_column(Text)


class Fact(Base):
    """An asserted value, attributable and time-bounded."""

    __tablename__ = "fact"
    __table_args__ = (Index("ix_fact_subject", "subject", "predicate"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("source.id"))
    source_fragment_id: Mapped[str | None] = mapped_column(ForeignKey("source_fragment.id"))
    subject: Mapped[str] = mapped_column(String(512))
    predicate: Mapped[str] = mapped_column(String(256))
    numeric_value: Mapped[float | None] = mapped_column(Float)
    text_value: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(64))
    confidence: Mapped[float | None] = mapped_column(Float)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Transformation(Base):
    """A step that derived data from other data, recorded so it can be replayed."""

    __tablename__ = "transformation"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(256))
    kind: Mapped[str] = mapped_column(String(64), default="compile")
    inputs: Mapped[list] = mapped_column(JSON, default=list)
    outputs: Mapped[list] = mapped_column(JSON, default=list)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class EvidenceLink(Base):
    """Attaches evidence to whatever it supports."""

    __tablename__ = "evidence_link"
    __table_args__ = (Index("ix_evidence_link_target", "target_kind", "target_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    evidence_id: Mapped[str] = mapped_column(ForeignKey("evidence.id", ondelete="CASCADE"))
    target_kind: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[str] = mapped_column(String(64))
    relation: Mapped[str] = mapped_column(String(64), default="supports")


class Explanation(Base):
    """A stored answer to 'why this decision?', as produced at run time."""

    __tablename__ = "explanation"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    solution_id: Mapped[str] = mapped_column(ForeignKey("solution.id", ondelete="CASCADE"))
    decision_key: Mapped[str] = mapped_column(String(512))
    narrative: Mapped[list] = mapped_column(JSON, default=list)
    graph: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AuditEvent(Base):
    """Append-only. Partitioned by time in PostgreSQL."""

    __tablename__ = "audit_event"
    __table_args__ = (Index("ix_audit_event_occurred", "occurred_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    actor: Mapped[str | None] = mapped_column(String(256))
    action: Mapped[str] = mapped_column(String(128))
    target_kind: Mapped[str | None] = mapped_column(String(64))
    target_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
