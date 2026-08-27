from __future__ import annotations

import pytest

from magi.new_bus import Contact, ContactRole


@pytest.mark.parametrize(
    ("role", "value"),
    [
        (ContactRole.MAGI, "magi"),
        (ContactRole.THIRD_PARTY_AGENT, "third_party_agent"),
    ],
)
def test_contact_parses_agent_roles(role: ContactRole, value: str) -> None:
    contact = Contact.parse({"name": "agent", "role": value})

    assert contact.role is role
