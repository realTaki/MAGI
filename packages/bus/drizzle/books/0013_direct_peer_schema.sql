-- Rename the identity field in place; retain contacts, profile roles and history.
ALTER TABLE books_contacts RENAME COLUMN asp_handle TO handle;
--> statement-breakpoint
DROP INDEX books_contacts_asp_handle_unique;
--> statement-breakpoint
CREATE UNIQUE INDEX books_contacts_handle_unique ON books_contacts(handle);
--> statement-breakpoint
ALTER TABLE books_contacts DROP COLUMN peer_trust;
--> statement-breakpoint
ALTER TABLE books_groups DROP COLUMN trust;
