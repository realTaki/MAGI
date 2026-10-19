ALTER TABLE jobs ADD COLUMN delivery_key text;
--> statement-breakpoint
CREATE UNIQUE INDEX jobs_delivery_key ON jobs(delivery_key);
