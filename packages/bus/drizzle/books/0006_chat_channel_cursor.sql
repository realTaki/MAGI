-- How far this workspace has read each chat's channel stream.
--
-- The channel-cursor Book became one field on the chat: a chat *is* a channel address, so
-- its reading position belongs to it. Written as a table rebuild instead of ``ALTER TABLE
-- … ADD COLUMN``: a workspace that loses its migration bookkeeping replays every migration
-- from the start (see ``0000_init.sql``), and SQLite has no ``ADD COLUMN IF NOT EXISTS``.
-- The copy names only the columns the old shape has, so a replay brings every reading back
-- as "nothing read yet" — the same trade-off ``0003_contact_channel_ids.sql`` makes for the
-- channel identities it adds. The old table is read before it is dropped, so a workspace
-- upgrading normally keeps the readings it has.
CREATE TABLE IF NOT EXISTS `books_chats_new` (
	`id` integer PRIMARY KEY NOT NULL,
	`channel` text NOT NULL,
	`delivery_address` text NOT NULL,
	`instruction` text DEFAULT '' NOT NULL,
	`topic` text DEFAULT '' NOT NULL,
	`info` text DEFAULT '' NOT NULL,
	`summary` text DEFAULT '' NOT NULL,
	`last_sequence` integer DEFAULT -1 NOT NULL
);
--> statement-breakpoint
INSERT INTO `books_chats_new` (`id`, `channel`, `delivery_address`, `instruction`, `topic`, `info`, `summary`, `last_sequence`)
SELECT `id`, `channel`, `delivery_address`, `instruction`, `topic`, `info`, `summary`,
	COALESCE((SELECT `last_sequence` FROM `books_channel_cursors`
		WHERE `books_channel_cursors`.`channel` = `books_chats`.`channel`
			AND `books_channel_cursors`.`address` = `books_chats`.`delivery_address`), -1)
FROM `books_chats`;
--> statement-breakpoint
DROP TABLE `books_chats`;
--> statement-breakpoint
ALTER TABLE `books_chats_new` RENAME TO `books_chats`;
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS `books_chats_channel_address` ON `books_chats` (`channel`,`delivery_address`);
--> statement-breakpoint
DROP TABLE IF EXISTS `books_channel_cursors`;
