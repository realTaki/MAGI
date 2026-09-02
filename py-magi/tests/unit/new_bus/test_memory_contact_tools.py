"""Memory and contact tools talk to BUS through existing Jobs."""

from __future__ import annotations

import json

from bus import Bus, MemoryKind, NoteKind
from tools.memory.contacts.add_contact import AddContactTool
from tools.memory.contacts.delete_contact_note import DeleteContactNoteTool
from tools.memory.contacts.save_contact_note import SaveContactNoteTool
from tools.memory.contacts.search_contacts import SearchContactsTool
from tools.memory.contacts.update_daily_note import UpdateDailyNoteTool
from tools.memory.core_memory.complete_memory import CompleteMemoryTool
from tools.memory.core_memory.delete_memory import DeleteMemoryTool
from tools.memory.core_memory.save_memory import SaveMemoryTool


def _ok(outcome) -> dict:
    assert outcome.is_error is False, outcome.content
    return json.loads(outcome.content)


async def test_save_complete_and_delete_memory(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    with Bus("@memory-tools", workspace=workspace) as bus:
        save = SaveMemoryTool(bus=bus)
        complete = CompleteMemoryTool(bus=bus)
        delete = DeleteMemoryTool(bus=bus)

        created = _ok(
            await save.run(topic="deadline", detail="ship Friday", kind=MemoryKind.LONG_TERM.value)
        )
        memory_id = created["created"]["id"]
        assert created["created"]["topic"] == "deadline"
        assert created["created"]["kind"] == MemoryKind.LONG_TERM.value

        patched = _ok(await save.run(memory_id=memory_id, detail="ship Monday"))
        assert patched["memory"]["detail"] == "ship Monday"
        assert patched["memory"]["topic"] == "deadline"

        archived = _ok(await complete.run(memory_id=memory_id))
        assert archived["memory"]["archived"] is True

        removed = _ok(await delete.run(memory_id=memory_id))
        assert removed["existed"] is True
        missing = _ok(await delete.run(memory_id=memory_id))
        assert missing["existed"] is False


async def test_contact_note_and_daily_flow(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    with Bus("@contact-tools", workspace=workspace) as bus:
        add = AddContactTool(bus=bus)
        save_note = SaveContactNoteTool(bus=bus)
        search = SearchContactsTool(bus=bus)
        daily = UpdateDailyNoteTool(bus=bus)
        delete_note = DeleteContactNoteTool(bus=bus)

        created = _ok(
            await add.run(name="Lily", nickname="Lil", role="assigned", notes="works in finance")
        )
        contact_id = created["created"]["id"]
        assert created["created"]["role"] == "authorized"
        assert created["initial_note"]["kind"] == NoteKind.PERMANENT.value

        extra = _ok(await save_note.run(contact_id=contact_id, note="prefers short replies"))
        note_id = extra["created"]["id"]
        updated = _ok(
            await save_note.run(note_id=note_id, note="prefers short replies, no emoji")
        )
        assert updated["updated"]["note"] == "prefers short replies, no emoji"

        hits = _ok(await search.run(query="finance"))
        assert any(item["id"] == contact_id for item in hits["contacts"])

        first = _ok(await daily.run(contact_id=contact_id, body_delta="sent the Q3 invoice"))
        assert first["created"] is True
        second = _ok(await daily.run(contact_id=contact_id, body_delta="Mark is OOO Friday"))
        assert second["created"] is False
        assert second["contact_note_id"] == first["contact_note_id"]

        listed = _ok(await search.run(query="invoice"))
        notes = next(item["notes"] for item in listed["contacts"] if item["id"] == contact_id)
        assert any("Q3 invoice" in (item.get("note") or "") for item in notes)
        assert any("OOO Friday" in (item.get("note") or "") for item in notes)

        removed = _ok(await delete_note.run(note_id=note_id))
        assert removed["existed"] is True
