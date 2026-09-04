-- Desktop (Electron) local SQLite. File lives in Electron userData as desktop.sqlite.
-- Operator-side settings and conversation list. Not the ASP session log, not a MAGI workspace.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at INTEGER NOT NULL
);

CREATE TABLE app_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    magi_id TEXT NOT NULL,
    remote_id TEXT,
    title TEXT NOT NULL DEFAULT '',
    sync_cursor TEXT,
    remote_updated_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE UNIQUE INDEX conversations_remote_id
    ON conversations(magi_id, remote_id)
    WHERE remote_id IS NOT NULL;
CREATE INDEX conversations_by_magi_updated
    ON conversations(magi_id, updated_at DESC);
