"""Internal MAGI-to-MAGI transport.

A2A uses its own durable ``sendA2AJob`` queue and worker. It is not a human
delivery channel, and it does not participate in Telegram/WebUI delivery
routing.
"""

__all__: list[str] = []
