ALTER TABLE books_contacts ADD COLUMN peer_id text;
--> statement-breakpoint
ALTER TABLE books_contacts ADD COLUMN peer_principal text;
--> statement-breakpoint
ALTER TABLE books_contacts ADD COLUMN peer_url text;
--> statement-breakpoint
ALTER TABLE books_contacts ADD COLUMN peer_trust text NOT NULL DEFAULT 'pending';
--> statement-breakpoint
CREATE UNIQUE INDEX books_contacts_peer_id ON books_contacts(peer_id);
--> statement-breakpoint
CREATE UNIQUE INDEX books_contacts_peer_principal ON books_contacts(peer_principal);
--> statement-breakpoint
ALTER TABLE books_chats ADD COLUMN peer_id text;
--> statement-breakpoint
ALTER TABLE books_chats ADD COLUMN conversation_uuid text;
--> statement-breakpoint
CREATE UNIQUE INDEX books_chats_peer_conversation ON books_chats(peer_id, conversation_uuid);
--> statement-breakpoint
CREATE TABLE books_deliveries (
 delivery_key text PRIMARY KEY NOT NULL,
 message_id integer NOT NULL UNIQUE REFERENCES books_messages(id) ON DELETE CASCADE,
 publisher text NOT NULL,
 metadata text,
 enqueue integer NOT NULL DEFAULT 1,
 job_id integer,
 attempts integer NOT NULL DEFAULT 0,
 next_attempt_at integer NOT NULL DEFAULT 0,
 last_error text
);
