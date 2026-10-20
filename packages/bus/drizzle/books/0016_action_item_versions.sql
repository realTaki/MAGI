-- Never reuse a deleted note's ID: a stale snapshot must not match a new note.
CREATE TABLE `books_action_items_versioned` (
  `id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
  `content` text NOT NULL,
  `version` integer DEFAULT 1 NOT NULL,
  `archived` integer DEFAULT false NOT NULL,
  `created_at` text DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
  `updated_at` text DEFAULT (CURRENT_TIMESTAMP) NOT NULL
);
--> statement-breakpoint
INSERT INTO `books_action_items_versioned` (`id`, `content`, `archived`, `created_at`, `updated_at`)
  SELECT `id`, `content`, `archived`, `created_at`, `updated_at` FROM `books_action_items`;
--> statement-breakpoint
DROP TABLE `books_action_items`;
--> statement-breakpoint
ALTER TABLE `books_action_items_versioned` RENAME TO `books_action_items`;
