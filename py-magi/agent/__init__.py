"""MAGI's message-driven Agent runtime.

Channels publish :class:`bus.ChatNotify`; :class:`agent.worker.AgentWorker`
consumes it and delegates LLM, tool, context, and delivery work through the
public Firmware JobBoards.
"""
