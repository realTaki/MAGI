"""Slots the demo conversation worker must hold before attach."""

from magi.new_bus import CreateConversationJob, Slot

REQUIRED_SLOTS: tuple[Slot, ...] = (Slot(CreateConversationJob, "publish"),)
