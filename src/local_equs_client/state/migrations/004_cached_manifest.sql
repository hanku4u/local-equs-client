-- C2.4: cache for /v1/manifest.json with the ETag for conditional GETs.
-- Single-row table: id is always 1.

CREATE TABLE cached_manifest (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    body_json   TEXT NOT NULL,
    etag        TEXT,
    fetched_at  TEXT NOT NULL
);
