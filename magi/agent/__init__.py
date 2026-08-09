"""MAGI's message-driven agent runtime — new_bus only.

Channels publish durable ``ChatJob`` inputs to ``agent_job_board``.
:class:`AgentWorker` consumes them, drives the agent loop, and delegates
LLM / tool / delivery effects to their respective job boards and workers.
"""
