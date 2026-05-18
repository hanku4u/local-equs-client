-- C5.11: pending telemetry events queued locally and flushed in batches.

CREATE TABLE telemetry_queue (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    type         TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE INDEX idx_telemetry_queue_created_at ON telemetry_queue(created_at);
