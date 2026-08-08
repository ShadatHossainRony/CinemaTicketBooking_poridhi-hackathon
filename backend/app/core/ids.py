"""Opaque public references.

`hold_id` and `booking_ref` ARE the access capability for their resource
(`04-api-contract.md` §6). They must not be enumerable. 128 bits of URL-safe
entropy from the stdlib — no `python-ulid` dependency required.
"""
from __future__ import annotations

import secrets

_PREFIX_HOLD = "hld_"
_PREFIX_BOOKING = "bk_"
_PREFIX_PAYMENT = "pay_"


def new_hold_id() -> str:
    return _PREFIX_HOLD + secrets.token_urlsafe(16)


def new_booking_ref() -> str:
    return _PREFIX_BOOKING + secrets.token_urlsafe(16)


def new_payment_id() -> str:
    """Used only as a fallback identifier; the gateway issues the real one."""
    return _PREFIX_PAYMENT + secrets.token_urlsafe(12)


def new_ticket_code(show_id: int, seat_label: str, suffix: str) -> str:
    """Short, human-friendly code for the QR ticket. NOT a secret."""
    return f"CS-{show_id}-{seat_label}-{suffix[:6].upper()}"
