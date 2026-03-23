CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS events (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    stream_id TEXT NOT NULL,
    stream_position BIGINT NOT NULL,
    global_position BIGINT GENERATED ALWAYS AS IDENTITY,
    event_type TEXT NOT NULL,
    event_version SMALLINT NOT NULL DEFAULT 1,
    payload JSONB NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT uq_stream_position UNIQUE (stream_id, stream_position)
);

CREATE INDEX IF NOT EXISTS idx_events_stream_id
    ON events (stream_id, stream_position);

CREATE INDEX IF NOT EXISTS idx_events_global_pos
    ON events (global_position);

CREATE INDEX IF NOT EXISTS idx_events_type
    ON events (event_type);

CREATE INDEX IF NOT EXISTS idx_events_recorded
    ON events (recorded_at);

CREATE TABLE IF NOT EXISTS event_streams (
    stream_id TEXT PRIMARY KEY,
    aggregate_type TEXT NOT NULL,
    current_version BIGINT NOT NULL DEFAULT -1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    archived_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS projection_checkpoints (
    projection_name TEXT PRIMARY KEY,
    last_position BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS projection_dead_letters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    projection_name TEXT NOT NULL,
    event_id UUID NOT NULL,
    stream_id TEXT NOT NULL,
    global_position BIGINT NOT NULL,
    event_type TEXT NOT NULL,
    event_version SMALLINT NOT NULL DEFAULT 1,
    event_data JSONB NOT NULL,
    error_type TEXT NOT NULL,
    error_message TEXT NOT NULL,
    attempts SMALLINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_projection_dead_letters_projection
    ON projection_dead_letters (projection_name, created_at DESC);

CREATE TABLE IF NOT EXISTS agent_checkpoints (
    session_id TEXT PRIMARY KEY,
    agent_type TEXT NOT NULL,
    application_id TEXT NOT NULL,
    last_completed_node TEXT,
    node_sequence BIGINT NOT NULL DEFAULT 0,
    checkpoint_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS outbox (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id UUID NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
    destination TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    published_at TIMESTAMPTZ,
    attempts SMALLINT NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_outbox_pending
    ON outbox (destination, published_at, created_at);
