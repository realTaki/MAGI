-- Contacts are numbered from 1, like everything else the workspace writes.
--
-- A workspace from an earlier release has the system contact at 0 and the MAGI at 1;
-- this moves them to 1 and 2 and follows every row that points at a contact. The guard
-- is what makes it replayable: once it has run nothing has id 0 any more, so a
-- workspace that lost its migration bookkeeping re-runs the whole file as a no-op.
UPDATE `books_messages` SET `contact_id` = 2 WHERE `contact_id` = 1 AND EXISTS (SELECT 1 FROM `books_contacts` WHERE `id` = 0);
--> statement-breakpoint
UPDATE `books_contact_notes` SET `contact_id` = 2 WHERE `contact_id` = 1 AND EXISTS (SELECT 1 FROM `books_contacts` WHERE `id` = 0);
--> statement-breakpoint
UPDATE `books_contacts` SET `id` = 2 WHERE `id` = 1 AND EXISTS (SELECT 1 FROM `books_contacts` WHERE `id` = 0);
--> statement-breakpoint
UPDATE `books_messages` SET `contact_id` = 1 WHERE `contact_id` = 0;
--> statement-breakpoint
UPDATE `books_contact_notes` SET `contact_id` = 1 WHERE `contact_id` = 0;
--> statement-breakpoint
UPDATE `books_contacts` SET `id` = 1 WHERE `id` = 0;
