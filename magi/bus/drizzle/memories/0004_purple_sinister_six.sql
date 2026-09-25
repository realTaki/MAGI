CREATE TABLE `books_conversation_members` (
	`id` integer PRIMARY KEY NOT NULL,
	`conversation_id` integer NOT NULL,
	`contact_id` integer NOT NULL,
	`added_at` text DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
	FOREIGN KEY (`contact_id`) REFERENCES `books_contacts`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE INDEX `books_conversation_members_contact` ON `books_conversation_members` (`contact_id`);--> statement-breakpoint
CREATE UNIQUE INDEX `books_conversation_members_pair` ON `books_conversation_members` (`conversation_id`,`contact_id`);