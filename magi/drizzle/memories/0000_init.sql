-- Baseline Book tables of one MAGI workspace.
-- ``IF NOT EXISTS`` keeps this a no-op for workspaces an earlier release already
-- created (they have these tables but no migration bookkeeping); the migration
-- is still recorded as applied, so every later one runs normally.
CREATE TABLE IF NOT EXISTS `books_contact_notes` (
	`id` integer PRIMARY KEY NOT NULL,
	`contact_id` integer NOT NULL,
	`note` text NOT NULL,
	`kind` text DEFAULT 'permanent' NOT NULL,
	`created_at` text DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
	FOREIGN KEY (`contact_id`) REFERENCES `books_contacts`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `books_contacts` (
	`id` integer PRIMARY KEY NOT NULL,
	`name` text NOT NULL,
	`nickname` text,
	`role` text DEFAULT 'stranger' NOT NULL,
	`last_seen_at` text DEFAULT (CURRENT_TIMESTAMP) NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS `books_contacts_name_unique` ON `books_contacts` (`name`);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `books_conversations` (
	`id` integer PRIMARY KEY NOT NULL,
	`channel` text NOT NULL,
	`delivery_address` text NOT NULL,
	`instruction` text DEFAULT '' NOT NULL,
	`topic` text DEFAULT '' NOT NULL,
	`info` text DEFAULT '' NOT NULL,
	`summary` text DEFAULT '' NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS `books_conversations_channel_address` ON `books_conversations` (`channel`,`delivery_address`);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `books_mcp_servers` (
	`name` text PRIMARY KEY NOT NULL,
	`connection_type` text NOT NULL,
	`command` text,
	`args` text DEFAULT '[]' NOT NULL,
	`url` text,
	`env` text DEFAULT '{}' NOT NULL,
	`headers` text DEFAULT '{}' NOT NULL,
	`enabled` integer DEFAULT 1 NOT NULL,
	`connect_timeout` real,
	`execute_timeout` real,
	`sse_read_timeout` real
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `books_memories` (
	`id` integer PRIMARY KEY NOT NULL,
	`topic` text NOT NULL,
	`detail` text NOT NULL,
	`kind` text DEFAULT 'temporary' NOT NULL,
	`archived` integer DEFAULT 0 NOT NULL,
	`created_at` text DEFAULT (CURRENT_TIMESTAMP) NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `books_messages` (
	`id` integer PRIMARY KEY NOT NULL,
	`conversation_id` integer NOT NULL,
	`contact_id` integer NOT NULL,
	`content` text NOT NULL,
	`created_at` text DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
	`archived` integer DEFAULT 0 NOT NULL
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `books_messages_conversation` ON `books_messages` (`conversation_id`,`id`);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `books_settings` (
	`key` text PRIMARY KEY NOT NULL,
	`value` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `books_tasks` (
	`id` integer PRIMARY KEY NOT NULL,
	`name` text NOT NULL,
	`prompt` text NOT NULL,
	`source` text DEFAULT 'user' NOT NULL,
	`enabled` integer DEFAULT 1 NOT NULL,
	`cron` text NOT NULL,
	`conversation_id` integer NOT NULL,
	`last_fired_minute` text
);
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS `books_tasks_name_unique` ON `books_tasks` (`name`);
