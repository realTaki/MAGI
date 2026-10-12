CREATE TABLE `asp_agents` (
	`handle` text PRIMARY KEY NOT NULL,
	`record_json` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `asp_chat_keys` (
	`creator` text NOT NULL,
	`key` text NOT NULL,
	`chat_id` text NOT NULL,
	`sequence` integer,
	PRIMARY KEY(`creator`, `key`)
);
--> statement-breakpoint
CREATE TABLE `asp_chats` (
	`id` text PRIMARY KEY NOT NULL,
	`record_json` text NOT NULL,
	`next_sequence` integer NOT NULL
);
--> statement-breakpoint
CREATE TABLE `asp_event_acks` (
	`event_id` text NOT NULL,
	`handle` text NOT NULL,
	PRIMARY KEY(`event_id`, `handle`),
	FOREIGN KEY (`event_id`) REFERENCES `asp_events`(`event_id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE TABLE `asp_events` (
	`chat_id` text NOT NULL,
	`sequence` integer NOT NULL,
	`event_id` text NOT NULL,
	`type` text NOT NULL,
	`created_at` integer NOT NULL,
	`payload_json` text NOT NULL,
	PRIMARY KEY(`chat_id`, `sequence`)
);
--> statement-breakpoint
CREATE UNIQUE INDEX `asp_events_event_id_unique` ON `asp_events` (`event_id`);--> statement-breakpoint
CREATE TABLE `asp_message_keys` (
	`chat_id` text NOT NULL,
	`sender` text NOT NULL,
	`key` text NOT NULL,
	`message_id` text NOT NULL,
	`sequence` integer NOT NULL,
	PRIMARY KEY(`chat_id`, `sender`, `key`)
);
--> statement-breakpoint
CREATE TABLE `asp_message_recipients` (
	`event_id` text NOT NULL,
	`handle` text NOT NULL,
	`acked` integer DEFAULT false NOT NULL,
	PRIMARY KEY(`event_id`, `handle`),
	FOREIGN KEY (`event_id`) REFERENCES `asp_events`(`event_id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE TABLE `asp_participants` (
	`chat_id` text NOT NULL,
	`handle` text NOT NULL,
	`record_json` text NOT NULL,
	PRIMARY KEY(`chat_id`, `handle`)
);
--> statement-breakpoint
CREATE TABLE `asp_settings` (
	`key` text PRIMARY KEY NOT NULL,
	`value_json` text NOT NULL,
	`updated_at` integer NOT NULL
);
