-- C2.2: generic key/value store for app-wide state.
-- First user is the stable client_id UUID created on first run.

CREATE TABLE app_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
