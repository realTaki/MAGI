"""Composition-Root deployment configuration.

The ``magi.deploy`` package holds the deployment-profile configuration
objects (path layouts, storage profiles, host binding policy).  Only
the launcher / Composition Root imports these — business modules
(``magi.bus.*``, ``magi.agent.*``, ``magi.tools.*``, ``magi.channels.*``)
must never import anything from here, because the deployment profile
is environment-specific and the architecture tests
(``tests/architecture/test_import_boundaries.py``) enforce this.

See ``docs/MAGI_LOCAL_STANDALONE_DEPLOYMENT_IMPLEMENTATION_PLAN.md`` §5.3.
"""

from magi.deploy.path_layout import LocalPathLayout

__all__ = ["LocalPathLayout"]