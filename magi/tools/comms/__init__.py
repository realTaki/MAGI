"""Outbound communication tools.

  - :mod:`magi.tools.comms.send_message` — push a
    message back to the operator (used by the agent
    loop when the LLM decides to speak proactively).
  - :mod:`magi.tools.comms.message_magi` — A2A
    schema-only effect: declares an outbound
    agent-to-agent intent without firing any local
    tool work.
"""