CREATE TABLE `asp_contacts` (
	`handle` text PRIMARY KEY NOT NULL,
	`token` text NOT NULL,
	`name` text,
	`nickname` text,
	`inbound_policy` text NOT NULL,
	`managed` integer NOT NULL
);
--> statement-breakpoint
INSERT INTO `asp_contacts` (`handle`, `token`, `name`, `nickname`, `inbound_policy`, `managed`)
SELECT `handle`, json_extract(`record_json`, '$.token'), json_extract(`record_json`, '$.name'),
	json_extract(`record_json`, '$.nickname'), COALESCE(json_extract(`record_json`, '$.inbound_policy'), 'open'),
	COALESCE(json_extract(`record_json`, '$.managed'), 0)
FROM `asp_agents`;
--> statement-breakpoint
CREATE UNIQUE INDEX `asp_contacts_token_unique` ON `asp_contacts` (`token`);
--> statement-breakpoint
CREATE TABLE `asp_contact_allowlist` (
	`contact_handle` text NOT NULL,
	`allowed_handle` text NOT NULL,
	PRIMARY KEY(`contact_handle`, `allowed_handle`),
	FOREIGN KEY (`contact_handle`) REFERENCES `asp_contacts`(`handle`) ON UPDATE cascade ON DELETE cascade
);
--> statement-breakpoint
INSERT INTO `asp_contact_allowlist` (`contact_handle`, `allowed_handle`)
SELECT `asp_agents`.`handle`, `entry`.`value`
FROM `asp_agents`, json_each(`asp_agents`.`record_json`, '$.allowlist') AS `entry`
WHERE `entry`.`type` = 'text';
--> statement-breakpoint
DROP TABLE `asp_agents`;
--> statement-breakpoint
ALTER TABLE `asp_chats` RENAME TO `__old_asp_chats`;
--> statement-breakpoint
CREATE TABLE `asp_chats` (
	`id` text PRIMARY KEY NOT NULL,
	`creator` text NOT NULL,
	`state` text NOT NULL,
	`topic` text,
	`created_at` integer NOT NULL,
	`ended_at` integer,
	`description` text,
	`kind` text,
	`next_sequence` integer NOT NULL
);
--> statement-breakpoint
INSERT INTO `asp_chats` (`id`, `creator`, `state`, `topic`, `created_at`, `ended_at`, `description`, `kind`, `next_sequence`)
SELECT `id`, json_extract(`record_json`, '$.creator'), json_extract(`record_json`, '$.state'),
	json_extract(`record_json`, '$.topic'), json_extract(`record_json`, '$.created_at'),
	json_extract(`record_json`, '$.ended_at'), json_extract(`record_json`, '$.description'),
	json_extract(`record_json`, '$.kind'), `next_sequence`
FROM `__old_asp_chats`;
--> statement-breakpoint
DROP TABLE `__old_asp_chats`;
--> statement-breakpoint
ALTER TABLE `asp_participants` RENAME TO `__old_asp_participants`;
--> statement-breakpoint
CREATE TABLE `asp_participants` (
	`chat_id` text NOT NULL,
	`handle` text NOT NULL,
	`status` text NOT NULL,
	`joined_at` integer,
	`left_at` integer,
	PRIMARY KEY(`chat_id`, `handle`)
);
--> statement-breakpoint
INSERT INTO `asp_participants` (`chat_id`, `handle`, `status`, `joined_at`, `left_at`)
SELECT `chat_id`, json_extract(`record_json`, '$.handle'), json_extract(`record_json`, '$.status'),
	json_extract(`record_json`, '$.joined_at'), json_extract(`record_json`, '$.left_at')
FROM `__old_asp_participants`;
--> statement-breakpoint
DROP TABLE `__old_asp_participants`;
