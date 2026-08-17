"""Outbound communication tools.

- :mod:`magi.tools.comms.send_message` — push a
  message back to the operator (used by the agent
  loop when the LLM decides to speak proactively).
  Bus plumbing lives on bus
  (``bus.conversations_book`` + ``bus.delivery_notify_job_board``).
- :mod:`magi.tools.comms.message_magi` — A2A schema-only effect. The
  AgentWorker persists it to shared MAGIS request/notify boards.
"""
