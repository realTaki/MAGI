CREATE TABLE books_groups (
 conversation_uuid text PRIMARY KEY NOT NULL,
 owner text NOT NULL,
 title text NOT NULL,
 epoch integer NOT NULL,
 trust text NOT NULL DEFAULT 'pending',
 chat_id integer NOT NULL UNIQUE REFERENCES books_chats(id)
);
--> statement-breakpoint
CREATE TABLE books_group_memberships (
 conversation_uuid text NOT NULL REFERENCES books_groups(conversation_uuid),
 epoch integer NOT NULL,
 envelope text NOT NULL,
 PRIMARY KEY(conversation_uuid, epoch)
);
--> statement-breakpoint
CREATE TABLE books_group_events (
 id integer PRIMARY KEY NOT NULL,
 conversation_uuid text NOT NULL REFERENCES books_groups(conversation_uuid),
 author text NOT NULL,
 event_id text NOT NULL,
 sequence integer NOT NULL,
 lamport integer NOT NULL,
 epoch integer NOT NULL,
 envelope text NOT NULL,
 message_id integer NOT NULL UNIQUE REFERENCES books_messages(id),
 UNIQUE(conversation_uuid, author, event_id),
 UNIQUE(conversation_uuid, author, sequence)
);
--> statement-breakpoint
CREATE INDEX books_group_events_order ON books_group_events(conversation_uuid, lamport, author, event_id);
