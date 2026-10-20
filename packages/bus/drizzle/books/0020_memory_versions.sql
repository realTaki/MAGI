-- Do not recycle deleted memory IDs: a stale id/version pair must never
-- accidentally name a new fact created after a foreground permanent deletion.
CREATE TABLE books_memories_versioned (
  id integer PRIMARY KEY AUTOINCREMENT,
  topic text NOT NULL,
  detail text NOT NULL,
  kind text NOT NULL DEFAULT 'temporary',
  archived integer NOT NULL DEFAULT 0,
  created_at text NOT NULL DEFAULT (CURRENT_TIMESTAMP),
  version integer NOT NULL DEFAULT 1
);
--> statement-breakpoint
INSERT INTO books_memories_versioned (id, topic, detail, kind, archived, created_at)
SELECT id, topic, detail, kind, archived, created_at FROM books_memories;
--> statement-breakpoint
DROP TABLE books_memories;
--> statement-breakpoint
ALTER TABLE books_memories_versioned RENAME TO books_memories;
