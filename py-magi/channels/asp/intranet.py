"""Intranet magi-asp: MAGI joins invites on receipt.

magi-asp binds loopback by default. A MAGI spawned onto that ASP already
holds a Bearer token for it, so an invite is joined immediately — no
public-network approval step and no config wizard.
"""

from __future__ import annotations

from ipaddress import ip_address
from urllib.parse import urlparse

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def is_intranet_origin(origin: str) -> bool:
    """True for loopback and RFC1918 magi-asp origins."""
    host = (urlparse(origin).hostname or "").lower()
    if host in _LOOPBACK_HOSTS:
        return True
    try:
        ip = ip_address(host)
    except ValueError:
        return False
    return bool(ip.is_loopback or ip.is_private or ip.is_link_local)


def should_join_on_invite(*, origin: str, invitee: str, handle: str) -> bool:
    """Join this ASP's invite without extra approval.

    `origin` is the ASP this MAGI is already attached to. Intranet is the
    default; a public hostname still joins because the token is this ASP's.
    """
    if invitee != handle:
        return False
    _ = origin  # attached origin; kept for call-site clarity and tests
    return True
