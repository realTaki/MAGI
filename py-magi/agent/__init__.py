"""MAGI's message-driven Agent runtime.

Channels publish :class:`bus.ChatNotify`; :class:`agent.worker.AgentWorker`
claims it and routes it to a per-conversation processor. That processor uses
the public Firmware JobBoards for LLM, tools, context, and delivery.
"""
