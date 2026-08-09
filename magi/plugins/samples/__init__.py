"""Sample plugins — concrete implementations of the protocol.

The package is intentionally empty until a NewBus-native plugin
contract is introduced. The former ``audit_log`` sample depended on
the retired Bus hook store and was removed with that runtime.

Adding a new plugin:

  1. Drop a new ``your_plugin.py`` into this package.
  2. Implement the :class:`magi.plugins.base.Plugin`
     protocol (an object with ``name``, ``version``,
     ``register(bus)``, and ``shutdown()``).
  3. Register it only through the future NewBus plugin contract.
"""

from __future__ import annotations

__all__: list[str] = []
