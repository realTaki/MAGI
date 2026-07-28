"""Restricted EVE runtime orchestrator.

Adam calls this service over an authenticated internal API.  Only this package
is granted Kubernetes RBAC; the agent container itself deliberately has none.
"""
