"""Outbound communication tools.

  - :mod:`magi.tools.comms.send_message` — push a
    message back to the operator (used by the agent
    loop when the LLM decides to speak proactively).
    Bus plumbing lives on bus
    (``bus.sessions_book`` + ``bus.delivery_job_board``).
  - :mod:`magi.tools.comms.message_magi` — A2A
    schema-only effect: declares an outbound
    agent-to-agent intent without firing any local
    tool work. The AgentWorker persists it onto
    ``bus.a2a_job_board`` (``a2a_jobs``) at
    transition-commit time.
"""
