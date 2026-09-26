-- Short-lived, recoverable agent execution context.  It belongs beside the Job
-- queue, not in Books: a completed turn may be inspected or removed without
-- becoming future conversational memory.
CREATE TABLE `agent_turns_cache` (
	`id` integer PRIMARY KEY NOT NULL,
	`turn_id` integer NOT NULL,
	`previous_block_id` integer,
	`sequence` integer NOT NULL,
	`kind` text NOT NULL,
	`job_id` integer,
	`payload` text NOT NULL,
	`created_at` text DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
	FOREIGN KEY (`turn_id`) REFERENCES `jobs`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`previous_block_id`) REFERENCES `agent_turns_cache`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`job_id`) REFERENCES `jobs`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE UNIQUE INDEX `agent_turns_cache_sequence` ON `agent_turns_cache` (`turn_id`,`sequence`);
--> statement-breakpoint
CREATE UNIQUE INDEX `agent_turns_cache_job` ON `agent_turns_cache` (`job_id`);
--> statement-breakpoint
CREATE INDEX `agent_turns_cache_turn` ON `agent_turns_cache` (`turn_id`,`id`);
