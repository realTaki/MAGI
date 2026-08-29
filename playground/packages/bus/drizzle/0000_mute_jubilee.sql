CREATE TABLE `jobs_call_llm` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`publisher` text NOT NULL,
	`worker` text,
	`status` text NOT NULL,
	`error` text,
	`messages` text NOT NULL,
	`contact_id` integer,
	`max_tokens` integer DEFAULT 1024 NOT NULL,
	`tools` text,
	`text` text DEFAULT '' NOT NULL,
	`thinking` text,
	`tool_uses` text,
	`raw_blocks` text,
	`finish_reason` text,
	`model` text DEFAULT '' NOT NULL,
	`error_code` text,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	`updated_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	CONSTRAINT "jobs_call_llm_status" CHECK("jobs_call_llm"."status" IN ('pending', 'claimed', 'completed', 'failed'))
);
--> statement-breakpoint
CREATE INDEX `jobs_call_llm_claim_idx` ON `jobs_call_llm` (`status`,`id`);--> statement-breakpoint
CREATE TABLE `jobs_get_setting` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`publisher` text NOT NULL,
	`status` text NOT NULL,
	`error` text,
	`key` text NOT NULL,
	`value` text,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	`updated_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	CONSTRAINT "jobs_get_setting_status" CHECK("jobs_get_setting"."status" IN ('completed', 'failed'))
);
--> statement-breakpoint
CREATE TABLE `books_settings` (
	`key` text PRIMARY KEY NOT NULL,
	`value` text NOT NULL
);
