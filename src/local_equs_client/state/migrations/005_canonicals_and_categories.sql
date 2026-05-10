-- C3.1: per-prc-group canonical sensor catalog + global category tree.

CREATE TABLE cached_canonical_sensors (
    prc_group_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    etag         TEXT,
    fetched_at   TEXT NOT NULL
);

CREATE TABLE cached_categories (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    payload_json TEXT NOT NULL,
    etag         TEXT,
    fetched_at   TEXT NOT NULL
);
