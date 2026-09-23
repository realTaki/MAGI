"""Copy an old running ASP's conversation history into the desktop SQLite.

Run before stopping an ASP version that kept session events only in memory.
No messages are acknowledged or deleted from ASP by this script.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen


def fetch(base: str, path: str, token: str | None = None) -> dict:
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    with urlopen(Request(f"{base.rstrip('/')}{path}", headers=headers), timeout=10) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asp", default="http://127.0.0.1:42069")
    parser.add_argument("--database", type=Path, default=Path.home() / ".magi" / "app" / "chat.sqlite")
    args = parser.parse_args()

    token = fetch(args.asp, "/operator")["token"]
    conversations = fetch(args.asp, "/conversations", token)["conversations"]
    snapshots = [
        (
            conversation,
            fetch(args.asp, f"/sessions/{quote(conversation['conversation_id'], safe='')}/events", token)["events"],
        )
        for conversation in conversations
    ]

    args.database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(args.database) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                record_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                conversation_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                record_json TEXT NOT NULL,
                PRIMARY KEY (conversation_id, sequence)
            );
            CREATE TABLE IF NOT EXISTS acknowledgements (
                conversation_id TEXT PRIMARY KEY,
                through_sequence INTEGER NOT NULL
            );
            """
        )
        for conversation, events in snapshots:
            conversation_id = conversation["conversation_id"]
            connection.execute(
                """INSERT INTO conversations VALUES (?, ?)
                   ON CONFLICT(id) DO UPDATE SET record_json = excluded.record_json""",
                (conversation_id, json.dumps(conversation)),
            )
            connection.executemany(
                "INSERT OR IGNORE INTO events VALUES (?, ?, ?)",
                (
                    (conversation_id, event["sequence"], json.dumps(event))
                    for event in events
                    if isinstance(event.get("sequence"), int)
                ),
            )
    print(f"Saved {len(snapshots)} conversations and {sum(len(events) for _, events in snapshots)} events to {args.database}")


if __name__ == "__main__":
    main()
