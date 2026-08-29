"""Telegram channel — outbound HTTP helpers.

The inbound python-telegram-bot listener lives in
:mod:`magi.channels.telegram.worker`. This package exists only to
host the shared outbound shims (``send_text_raw`` /
``verify_token``) the worker (and the onboarding route) call.
"""

from __future__ import annotations

from magi.channels.telegram.bot import send_text_raw, verify_token

__all__ = ["send_text_raw", "verify_token"]
