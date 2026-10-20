CREATE TABLE `books_action_items` (
  `id` integer PRIMARY KEY NOT NULL,
  `content` text NOT NULL,
  `archived` integer DEFAULT false NOT NULL,
  `created_at` text DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
  `updated_at` text DEFAULT (CURRENT_TIMESTAMP) NOT NULL
);
