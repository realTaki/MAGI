from __future__ import annotations

import dataclasses

import pytest

from bus import Contact, ContactRole


@pytest.mark.parametrize(
    ("role", "value"),
    [
        (ContactRole.AUTHORIZED, "authorized"),
        (ContactRole.STRANGER, "stranger"),
        (ContactRole.MAGI, "magi"),
        (ContactRole.THIRD_PARTY_AGENT, "third_party_agent"),
    ],
)
def test_contact_parses_agent_roles(role: ContactRole, value: str) -> None:
    contact = Contact.parse({"name": "agent", "role": value})

    assert contact.role is role


def test_contact_has_no_channel_specific_identity() -> None:
    assert "tgid" not in {field.name for field in dataclasses.fields(Contact)}
