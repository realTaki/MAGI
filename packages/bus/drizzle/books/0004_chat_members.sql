-- Who is in a chat.
--
-- A chat is a channel address and an address holds people, so the channels record
-- whoever they hear from here and the agent reads the result into its context. ``IF NOT
-- EXISTS`` keeps a replay of this file harmless, the same way ``0000_init.sql`` does.
CREATE TABLE IF NOT EXISTS `books_chat_members` (
	`id` integer PRIMARY KEY NOT NULL,
	`chat_id` integer NOT NULL,
	`contact_id` integer NOT NULL,
	`added_at` text DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
	FOREIGN KEY (`contact_id`) REFERENCES `books_contacts`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `books_chat_members_contact` ON `books_chat_members` (`contact_id`);
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS `books_chat_members_pair` ON `books_chat_members` (`chat_id`,`contact_id`);
