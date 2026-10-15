-- `content` is the original message text. The LLM contact envelope is assembled
-- in the agent module. Rebuild instead of DROP COLUMN so a workspace that
-- replays its migrations keeps foreign keys, and so stored user envelopes
-- can be stripped from the start of `content`.
CREATE TABLE IF NOT EXISTS `books_messages_new` (
	`id` integer PRIMARY KEY NOT NULL,
	`chat_id` integer NOT NULL,
	`contact_id` integer NOT NULL,
	`content` text NOT NULL,
	`created_at` text DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
	`archived` integer DEFAULT 0 NOT NULL,
	FOREIGN KEY (`chat_id`) REFERENCES `books_chats`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`contact_id`) REFERENCES `books_contacts`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
INSERT INTO `books_messages_new` (`id`, `chat_id`, `contact_id`, `content`, `created_at`, `archived`)
SELECT `id`, `chat_id`, `contact_id`,
	CASE
		WHEN `content` LIKE '[contact id % | %]' || char(10) || '%'
		THEN substr(`content`, instr(`content`, char(10)) + 1)
		ELSE `content`
	END,
	`created_at`, `archived` FROM `books_messages`;
--> statement-breakpoint
DROP TABLE `books_messages`;
--> statement-breakpoint
ALTER TABLE `books_messages_new` RENAME TO `books_messages`;
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `books_messages_chat` ON `books_messages` (`chat_id`,`id`);
