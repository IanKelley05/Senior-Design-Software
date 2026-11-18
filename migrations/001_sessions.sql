CREATE TABLE IF NOT EXISTS training_sessions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 100),
    started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    ended_at timestamptz CHECK (ended_at >= started_at)
);

-- All active rows share one unique key, allowing at most one active session.
CREATE UNIQUE INDEX IF NOT EXISTS one_active_session
    ON training_sessions ((true)) WHERE ended_at IS NULL;

CREATE TABLE IF NOT EXISTS hits (
    event_id uuid PRIMARY KEY,
    session_id bigint NOT NULL REFERENCES training_sessions(id),
    timestamp timestamptz NOT NULL,
    region text NOT NULL CHECK (region ~ '^[a-z][a-z0-9-]{0,39}$'),
    force_n double precision NOT NULL
        CHECK (force_n >= 0 AND force_n < 'Infinity'::double precision)
);
CREATE INDEX IF NOT EXISTS hits_by_session ON hits(session_id, timestamp, event_id);
