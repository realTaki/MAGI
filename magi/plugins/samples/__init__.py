"""Sample plugins — concrete implementations of the protocol.

The package is intentionally empty until a Bus-native plugin
contract is introduced. The former ``audit_log`` sample depended on
the old hook store and was removed with that runtime.

Adding a new plugin:

  1. Drop a new ``your_plugin.py`` into this package.
  2. Implement the :class:`magi.plugins.base.Plugin`
     protocol (an object with ``name``, ``version``,
     ``register(bus)``, and ``shutdown()``).
  3. Register it only through the future Bus plugin contract.
"""

from __future__ import annotations

__all__: list[str] = []
