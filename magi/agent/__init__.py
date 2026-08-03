"""MAGI's message-driven agent runtime.

Channels publish durable inputs to :mod:`magi.bus`.  :class:`AgentWorker`
executes one provider step at a time, persists the transition, and delegates
tool and delivery effects to their own workers.
"""
