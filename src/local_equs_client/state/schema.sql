-- Canonical reference for the cumulative client state schema.
-- The runtime applies versioned files in `migrations/`; this file is kept
-- in sync as a single-glance overview for code review.

-- Migration bookkeeping. Created by the migrator before any user migration runs.
CREATE TABLE schema_version (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT    NOT NULL
);

-- migrations/001_initial.sql

-- migrations/002_local_files_size.sql adds size_bytes.
CREATE TABLE local_files (
    file_id      TEXT    PRIMARY KEY,
    tool_id      TEXT    NOT NULL,
    hour_bucket  TEXT,
    min_ts       REAL    NOT NULL,
    max_ts       REAL    NOT NULL,
    row_count    INTEGER NOT NULL,
    sha256       TEXT,
    pinned       INTEGER NOT NULL DEFAULT 0,
    archived     INTEGER NOT NULL DEFAULT 0,
    size_bytes   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_local_files_tool_time ON local_files (tool_id, min_ts, max_ts);

CREATE TABLE saved_views (
    view_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL UNIQUE,
    payload_json TEXT    NOT NULL,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
);

CREATE TABLE saved_sets (
    set_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL UNIQUE,
    payload_json TEXT    NOT NULL,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
);

CREATE TABLE cached_sensors (
    tool_id      TEXT    PRIMARY KEY,
    payload_json TEXT    NOT NULL,
    etag         TEXT,
    fetched_at   TEXT    NOT NULL
);

CREATE TABLE cached_mappings (
    prc_group_id TEXT    PRIMARY KEY,
    payload_json TEXT    NOT NULL,
    etag         TEXT,
    fetched_at   TEXT    NOT NULL
);

-- migrations/003_app_state.sql adds the generic app-wide key/value store.
CREATE TABLE app_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
