-- The initial migration deliberately adopts the hand-written chat.sqlite schema.
-- IF NOT EXISTS lets Drizzle record this baseline for existing desktop profiles
-- without replacing cache rows, acknowledgements, or queued outgoing messages.
CREATE TABLE IF NOT EXISTS `chats` (
	`id` text PRIMARY KEY NOT NULL,
	`record_json` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `events` (
	`chat_id` text NOT NULL,
	`sequence` integer NOT NULL,
	`record_json` text NOT NULL,
	PRIMARY KEY(`chat_id`, `sequence`)
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `acknowledgements` (
	`chat_id` text PRIMARY KEY NOT NULL,
	`through_sequence` integer NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `outgoing_messages` (
	`id` text PRIMARY KEY NOT NULL,
	`chat_id` text NOT NULL,
	`content` text NOT NULL,
	`created_at` integer NOT NULL
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `outgoing_messages_order` ON `outgoing_messages` (`created_at`,`id`);
