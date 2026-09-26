-- Add the missing Book relationships. These are table rebuilds rather than ALTERs so
-- an older workspace that lost migration bookkeeping can replay the migration safely.
CREATE TABLE IF NOT EXISTS `books_chat_members_new` (
	`id` integer PRIMARY KEY NOT NULL,
	`chat_id` integer NOT NULL,
	`contact_id` integer NOT NULL,
	`added_at` text DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
	FOREIGN KEY (`chat_id`) REFERENCES `books_chats`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`contact_id`) REFERENCES `books_contacts`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
INSERT INTO `books_chat_members_new` (`id`, `chat_id`, `contact_id`, `added_at`)
SELECT `id`, `chat_id`, `contact_id`, `added_at` FROM `books_chat_members`;
--> statement-breakpoint
DROP TABLE `books_chat_members`;
--> statement-breakpoint
ALTER TABLE `books_chat_members_new` RENAME TO `books_chat_members`;
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `books_chat_members_contact` ON `books_chat_members` (`contact_id`);
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS `books_chat_members_pair` ON `books_chat_members` (`chat_id`,`contact_id`);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `books_messages_new` (
	`id` integer PRIMARY KEY NOT NULL,
	`chat_id` integer NOT NULL,
	`contact_id` integer NOT NULL,
	`llm_role` text DEFAULT 'user' NOT NULL,
	`llm_content` text DEFAULT '' NOT NULL,
	`content` text NOT NULL,
	`created_at` text DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
	`archived` integer DEFAULT 0 NOT NULL,
	FOREIGN KEY (`chat_id`) REFERENCES `books_chats`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`contact_id`) REFERENCES `books_contacts`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
INSERT INTO `books_messages_new` (`id`, `chat_id`, `contact_id`, `llm_role`, `llm_content`, `content`, `created_at`, `archived`)
SELECT `id`, `chat_id`, `contact_id`, `llm_role`, `llm_content`, `content`, `created_at`, `archived` FROM `books_messages`;
--> statement-breakpoint
DROP TABLE `books_messages`;
--> statement-breakpoint
ALTER TABLE `books_messages_new` RENAME TO `books_messages`;
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `books_messages_chat` ON `books_messages` (`chat_id`,`id`);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `books_tasks_new` (
	`id` integer PRIMARY KEY NOT NULL,
	`name` text NOT NULL,
	`prompt` text NOT NULL,
	`source` text DEFAULT 'user' NOT NULL,
	`enabled` integer DEFAULT 1 NOT NULL,
	`cron` text NOT NULL,
	`chat_id` integer NOT NULL,
	`last_fired_minute` text,
	FOREIGN KEY (`chat_id`) REFERENCES `books_chats`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
INSERT INTO `books_tasks_new` (`id`, `name`, `prompt`, `source`, `enabled`, `cron`, `chat_id`, `last_fired_minute`)
SELECT `id`, `name`, `prompt`, `source`, `enabled`, `cron`, `chat_id`, `last_fired_minute` FROM `books_tasks`;
--> statement-breakpoint
DROP TABLE `books_tasks`;
--> statement-breakpoint
ALTER TABLE `books_tasks_new` RENAME TO `books_tasks`;
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS `books_tasks_name_unique` ON `books_tasks` (`name`);
