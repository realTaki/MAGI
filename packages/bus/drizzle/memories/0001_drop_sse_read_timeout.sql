-- Drop the unused `sse_read_timeout` column.
--
-- It is written as a table rebuild instead of ``ALTER TABLE … DROP COLUMN`` so
-- that replaying it is harmless: a workspace that lost its migration bookkeeping
-- re-runs every migration from the start (see ``0000_init.sql``), and SQLite has
-- no ``DROP COLUMN IF EXISTS``. The copy names its columns, so it works whether
-- or not the old table still has the column.
CREATE TABLE IF NOT EXISTS `books_mcp_servers_new` (
	`name` text PRIMARY KEY NOT NULL,
	`connection_type` text NOT NULL,
	`command` text,
	`args` text DEFAULT '[]' NOT NULL,
	`url` text,
	`env` text DEFAULT '{}' NOT NULL,
	`headers` text DEFAULT '{}' NOT NULL,
	`enabled` integer DEFAULT 1 NOT NULL,
	`connect_timeout` real,
	`execute_timeout` real
);
--> statement-breakpoint
INSERT INTO `books_mcp_servers_new` (`name`, `connection_type`, `command`, `args`, `url`, `env`, `headers`, `enabled`, `connect_timeout`, `execute_timeout`)
SELECT `name`, `connection_type`, `command`, `args`, `url`, `env`, `headers`, `enabled`, `connect_timeout`, `execute_timeout` FROM `books_mcp_servers`;
--> statement-breakpoint
DROP TABLE `books_mcp_servers`;
--> statement-breakpoint
ALTER TABLE `books_mcp_servers_new` RENAME TO `books_mcp_servers`;
