-- Range partitioning for the three tables that grow without bound.
--
-- run_log, event and audit_event accumulate forever while everything else in
-- the schema stays roughly proportional to the number of problems. Partitioning
-- by month means retention is a DETACH rather than a DELETE of millions of
-- rows, and queries that name a time range touch only the partitions they need.
--
-- Run this on a fresh database, before the tables carry data: it recreates them.

BEGIN;

-- ------------------------------------------------------------- audit_event

ALTER TABLE audit_event RENAME TO audit_event_unpartitioned;

CREATE TABLE audit_event (
    id          varchar(36) NOT NULL,
    actor       varchar(256),
    action      varchar(128) NOT NULL,
    target_kind varchar(64),
    target_id   varchar(64),
    detail      jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    -- The partition key must be part of every unique constraint, so the primary
    -- key is composite rather than the id alone.
    PRIMARY KEY (id, occurred_at)
) PARTITION BY RANGE (occurred_at);

CREATE INDEX ix_audit_event_occurred ON audit_event (occurred_at DESC);
CREATE INDEX ix_audit_event_target   ON audit_event (target_kind, target_id);

INSERT INTO audit_event SELECT * FROM audit_event_unpartitioned;
DROP TABLE audit_event_unpartitioned;

-- ------------------------------------------------------------------ event

ALTER TABLE event RENAME TO event_unpartitioned;

CREATE TABLE event (
    id            varchar(36) NOT NULL,
    event_type_id varchar(36) NOT NULL REFERENCES event_type(id),
    entity_id     varchar(36) REFERENCES entity(id),
    occurred_at   timestamptz NOT NULL DEFAULT now(),
    payload       jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (id, occurred_at)
) PARTITION BY RANGE (occurred_at);

CREATE INDEX ix_event_occurred ON event (occurred_at DESC);
CREATE INDEX ix_event_entity   ON event (entity_id, occurred_at DESC);

INSERT INTO event SELECT * FROM event_unpartitioned;
DROP TABLE event_unpartitioned;

-- ---------------------------------------------------------------- run_log

ALTER TABLE run_log RENAME TO run_log_unpartitioned;

CREATE TABLE run_log (
    id        varchar(36) NOT NULL,
    run_id    varchar(36) NOT NULL,
    sequence  integer NOT NULL DEFAULT 0,
    level     varchar(16) NOT NULL DEFAULT 'info',
    message   text NOT NULL,
    logged_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (id, logged_at)
) PARTITION BY RANGE (logged_at);

CREATE INDEX ix_run_log_run ON run_log (run_id, sequence);

INSERT INTO run_log SELECT * FROM run_log_unpartitioned;
DROP TABLE run_log_unpartitioned;

-- --------------------------------------------------- partition maintenance

-- Creates the monthly partition covering a given moment, if it is missing.
-- Call it from a scheduled job a month ahead; a partitioned table with no
-- partition for "now" rejects inserts, which is a bad way to learn about this.
CREATE OR REPLACE FUNCTION ensure_month_partition(base_table text, at timestamptz)
RETURNS text AS $$
DECLARE
    start_of_month date := date_trunc('month', at)::date;
    next_month     date := (date_trunc('month', at) + interval '1 month')::date;
    partition_name text := format('%s_%s', base_table, to_char(start_of_month, 'YYYY_MM'));
BEGIN
    IF to_regclass(partition_name) IS NULL THEN
        EXECUTE format(
            'CREATE TABLE %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)',
            partition_name, base_table, start_of_month, next_month
        );
    END IF;
    RETURN partition_name;
END;
$$ LANGUAGE plpgsql;

-- Cover the current month and the next three, so a fresh install accepts writes.
DO $$
DECLARE
    target text;
    offset_months integer;
BEGIN
    FOREACH target IN ARRAY ARRAY['audit_event', 'event', 'run_log'] LOOP
        FOR offset_months IN 0..3 LOOP
            PERFORM ensure_month_partition(
                target, now() + (offset_months || ' months')::interval
            );
        END LOOP;
    END LOOP;
END $$;

COMMIT;
