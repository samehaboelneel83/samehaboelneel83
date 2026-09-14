-- PostgreSQL-specific schema upgrades.
--
-- The ORM creates portable tables that run on SQLite as well as PostgreSQL.
-- This migration adds the things PostgreSQL can do that the ORM cannot express
-- portably, and that this platform genuinely needs:
--
--   * JSONB instead of JSON, so flexible attributes are indexable
--   * ltree, so organisation and hierarchy paths support ancestor queries
--   * PostGIS, so entities have real geography rather than two float columns
--   * range partitioning on the three tables that grow without bound
--
-- Run after the ORM has created the base tables (or after your migration tool
-- has done so), and run it once.

BEGIN;

CREATE EXTENSION IF NOT EXISTS ltree;
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------------------------------------------------------------- JSONB

-- JSON stores text and re-parses it on every access; JSONB stores a decomposed
-- binary form that can be indexed. Flexible attributes are queried constantly,
-- so the conversion pays for itself immediately.
ALTER TABLE entity            ALTER COLUMN attributes TYPE jsonb USING attributes::jsonb;
ALTER TABLE entity_type       ALTER COLUMN schema     TYPE jsonb USING schema::jsonb;
ALTER TABLE relationship      ALTER COLUMN attributes TYPE jsonb USING attributes::jsonb;
ALTER TABLE entity_state      ALTER COLUMN attributes TYPE jsonb USING attributes::jsonb;
ALTER TABLE event             ALTER COLUMN payload    TYPE jsonb USING payload::jsonb;
ALTER TABLE problem           ALTER COLUMN spec       TYPE jsonb USING spec::jsonb;
ALTER TABLE model_version     ALTER COLUMN ir         TYPE jsonb USING ir::jsonb;
ALTER TABLE model_version     ALTER COLUMN compilation_record TYPE jsonb USING compilation_record::jsonb;
ALTER TABLE model_artifact    ALTER COLUMN content    TYPE jsonb USING content::jsonb;
ALTER TABLE solution          ALTER COLUMN payload    TYPE jsonb USING payload::jsonb;
ALTER TABLE explanation       ALTER COLUMN graph      TYPE jsonb USING graph::jsonb;
ALTER TABLE audit_event       ALTER COLUMN detail     TYPE jsonb USING detail::jsonb;

CREATE INDEX IF NOT EXISTS ix_entity_attributes   ON entity       USING gin (attributes jsonb_path_ops);
CREATE INDEX IF NOT EXISTS ix_problem_spec        ON problem      USING gin (spec jsonb_path_ops);
CREATE INDEX IF NOT EXISTS ix_audit_detail        ON audit_event  USING gin (detail jsonb_path_ops);
CREATE INDEX IF NOT EXISTS ix_entity_name_trgm    ON entity       USING gin (name gin_trgm_ops);

-- ---------------------------------------------------------------- ltree

-- A materialised path with a GiST index answers "everything under this unit"
-- in one indexed operation. The closure table stays: it answers the same
-- questions on any engine, and it is what keeps the ORM portable.
ALTER TABLE organization   ALTER COLUMN path TYPE ltree USING NULLIF(path, '')::ltree;
ALTER TABLE hierarchy_node ALTER COLUMN path TYPE ltree USING NULLIF(path, '')::ltree;

CREATE INDEX IF NOT EXISTS ix_organization_path   ON organization   USING gist (path);
CREATE INDEX IF NOT EXISTS ix_hierarchy_node_gist ON hierarchy_node USING gist (path);

-- --------------------------------------------------------------- PostGIS

-- Two float columns cannot answer "within 50km of here"; a geography column
-- with a spatial index can, and routing problems ask exactly that.
ALTER TABLE entity ADD COLUMN IF NOT EXISTS location geography(Point, 4326);

UPDATE entity
   SET location = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography
 WHERE latitude IS NOT NULL
   AND longitude IS NOT NULL
   AND location IS NULL;

CREATE INDEX IF NOT EXISTS ix_entity_location ON entity USING gist (location);

-- Keep the float columns in step, so a client that only understands lat/long
-- still sees the truth after a spatial update.
CREATE OR REPLACE FUNCTION entity_sync_location() RETURNS trigger AS $$
BEGIN
    IF NEW.location IS NOT NULL THEN
        NEW.latitude  := ST_Y(NEW.location::geometry);
        NEW.longitude := ST_X(NEW.location::geometry);
    ELSIF NEW.latitude IS NOT NULL AND NEW.longitude IS NOT NULL THEN
        NEW.location := ST_SetSRID(ST_MakePoint(NEW.longitude, NEW.latitude), 4326)::geography;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_entity_sync_location ON entity;
CREATE TRIGGER trg_entity_sync_location
    BEFORE INSERT OR UPDATE OF latitude, longitude, location ON entity
    FOR EACH ROW EXECUTE FUNCTION entity_sync_location();

COMMIT;
