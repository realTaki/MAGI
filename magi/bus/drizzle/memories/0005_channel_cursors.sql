-- How far this workspace has read each channel's stream.
--
-- ASP numbers every session event and replays whatever it has not seen acknowledged, so
-- the reader needs to know which numbers it has already taken in. ``IF NOT EXISTS`` keeps
-- a replay of this file harmless, the same way ``0000_init.sql`` does.
CREATE TABLE IF NOT EXISTS `books_channel_cursors` (
	`id` integer PRIMARY KEY NOT NULL,
	`channel` text NOT NULL,
	`address` text NOT NULL,
	`last_sequence` integer DEFAULT 0 NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS `books_channel_cursors_target` ON `books_channel_cursors` (`channel`,`address`);
