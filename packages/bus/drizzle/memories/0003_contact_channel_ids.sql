-- A contact can be known by more than one channel identity.
--
-- Written as a table rebuild instead of ``ALTER TABLE … ADD COLUMN``: a workspace that
-- loses its migration bookkeeping replays this file from the start, and SQLite has no
-- ``ADD COLUMN IF NOT EXISTS``. The copy names only the columns the old shape has, so the
-- identities come back empty on a replay — the next message re-learns them from the
-- channel it arrived on, which is also what fills them in for an older workspace.
CREATE TABLE IF NOT EXISTS `books_contacts_new` (
	`id` integer PRIMARY KEY NOT NULL,
	`name` text NOT NULL,
	`nickname` text,
	`role` text DEFAULT 'stranger' NOT NULL,
	`tg_id` text,
	`asp_handle` text,
	`last_seen_at` text DEFAULT (CURRENT_TIMESTAMP) NOT NULL
);
--> statement-breakpoint
INSERT INTO `books_contacts_new` (`id`, `name`, `nickname`, `role`, `last_seen_at`)
SELECT `id`, `name`, `nickname`, `role`, `last_seen_at` FROM `books_contacts`;
--> statement-breakpoint
DROP TABLE `books_contacts`;
--> statement-breakpoint
ALTER TABLE `books_contacts_new` RENAME TO `books_contacts`;
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS `books_contacts_name_unique` ON `books_contacts` (`name`);
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS `books_contacts_tg_id_unique` ON `books_contacts` (`tg_id`);
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS `books_contacts_asp_handle_unique` ON `books_contacts` (`asp_handle`);
--> statement-breakpoint
UPDATE `books_contacts` SET `asp_handle` = 'user' WHERE `id` = 1 AND `asp_handle` IS NULL;
