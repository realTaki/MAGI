ALTER TABLE `books_action_items` ADD `category` text DEFAULT 'todo' NOT NULL CONSTRAINT `action_item_category` CHECK (`category` IN ('todo', 'idea', 'goal'));
