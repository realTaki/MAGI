CREATE TABLE `relay_principals` (
	`handle` text PRIMARY KEY NOT NULL,
	`token` text NOT NULL,
	`inbound_policy` text NOT NULL,
	`managed` integer NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `relay_principals_token_unique` ON `relay_principals` (`token`);
--> statement-breakpoint
CREATE TABLE `relay_allowlist` (
	`contact_handle` text NOT NULL,
	`allowed_handle` text NOT NULL,
	PRIMARY KEY(`contact_handle`, `allowed_handle`)
);
--> statement-breakpoint
CREATE TABLE `relay_chats` (
	`id` text PRIMARY KEY NOT NULL,
	`creator` text NOT NULL,
	`state` text NOT NULL,
	`created_at` integer NOT NULL,
	`ended_at` integer,
	`description` text,
	`kind` text,
	`next_sequence` integer NOT NULL
);
--> statement-breakpoint
CREATE TABLE `relay_participants` (
	`chat_id` text NOT NULL,
	`handle` text NOT NULL,
	`status` text NOT NULL,
	`joined_at` integer,
	`left_at` integer,
	PRIMARY KEY(`chat_id`, `handle`)
);
