"""Deprecated import path for internal session models.

Agent code must not use these SQLAlchemy classes. They remain importable only
for migration tooling while the implementation is moved to BUS repositories.
"""

from magi.bus.models.local.session import ChatMessage, ChatSession

__all__ = ["ChatMessage", "ChatSession"]
