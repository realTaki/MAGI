-- Store the exact role/content pair that the Agent sends to the LLM, while retaining
-- `content` as the original channel text for searches and delivery. This is a rebuild
-- so a workspace that lost migration bookkeeping can replay it safely.
CREATE TABLE IF NOT EXISTS `books_messages_new` (
	`id` integer PRIMARY KEY NOT NULL,
	`chat_id` integer NOT NULL,
	`contact_id` integer NOT NULL,
	`llm_role` text DEFAULT 'user' NOT NULL,
	`llm_content` text DEFAULT '' NOT NULL,
	`content` text NOT NULL,
	`created_at` text DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
	`archived` integer DEFAULT 0 NOT NULL
);
--> statement-breakpoint
INSERT INTO `books_messages_new` (`id`, `chat_id`, `contact_id`, `llm_role`, `llm_content`, `content`, `created_at`, `archived`)
SELECT `id`, `chat_id`, `contact_id`,
	CASE WHEN `contact_id` = 2 THEN 'assistant' ELSE 'user' END,
	CASE
		WHEN `contact_id` = 2 THEN `content`
		WHEN `content` LIKE '[contact id % | %]%' THEN `content`
		ELSE '[contact id ' || `contact_id` || ' | ' || `created_at` || ']' || char(10) || `content`
	END,
	`content`, `created_at`, `archived`
FROM `books_messages`;
--> statement-breakpoint
DROP TABLE `books_messages`;
--> statement-breakpoint
ALTER TABLE `books_messages_new` RENAME TO `books_messages`;
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `books_messages_chat` ON `books_messages` (`chat_id`,`id`);
