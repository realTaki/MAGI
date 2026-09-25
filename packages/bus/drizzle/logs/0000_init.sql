-- Baseline job queue of one MAGI workspace. ``IF NOT EXISTS`` keeps it a no-op
-- for workspaces an earlier release already created.
CREATE TABLE IF NOT EXISTS `jobs` (
	`id` integer PRIMARY KEY NOT NULL,
	`type` text NOT NULL,
	`publisher` text NOT NULL,
	`status` text DEFAULT 'pending' NOT NULL,
	`worker` text,
	`input` text NOT NULL,
	`output` text,
	`error` text,
	`created_at` text DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
	`updated_at` text DEFAULT (CURRENT_TIMESTAMP) NOT NULL
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `jobs_claim` ON `jobs` (`type`,`status`,`id`);
