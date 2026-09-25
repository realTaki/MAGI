DROP TABLE `books_channel_cursors`;--> statement-breakpoint
ALTER TABLE `books_chats` ADD `last_sequence` integer DEFAULT -1 NOT NULL;