-- C1.2: store on-disk size so total_size_bytes() and the Local Library panel
-- can answer in O(1) without stat-ing every file.

ALTER TABLE local_files ADD COLUMN size_bytes INTEGER NOT NULL DEFAULT 0;
