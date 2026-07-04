"""
Input validation helpers.

Provides lightweight validators for Solana addresses, numeric ranges,
and other domain primitives so that every module validates consistently.
"""

from __future__ import annotations

import re


# ── Solana ────────────────────────────────────────────────────────────────────

_BASE58_PATTERN = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def is_valid_solana_address(address: str) -> bool:
    """
    Return True when ``address`` looks like a valid Solana public key.

    Performs a structural check only (base-58 character set, correct
    length) without verifying the key on-chain.

    Parameters
    ----------
    address:
        The public key string to validate.
    """
    if not isinstance(address, str):
        return False
    return bool(_BASE58_PATTERN.match(address.strip()))


def is_valid_token_mint(mint: str) -> bool:
    """
    Alias for :func:`is_valid_solana_address`.

    Both token mints and wallet addresses share the same format in Solana.
    """
    return is_valid_solana_address(mint)


# ── Numeric ───────────────────────────────────────────────────────────────────

def clamp(value: float, minimum: float, maximum: float) -> float:
    """
    Clamp ``value`` to the inclusive range [minimum, maximum].

    Parameters
    ----------
    value:
        The number to clamp.
    minimum:
        Lower bound (inclusive).
    maximum:
        Upper bound (inclusive).
    """
    return max(minimum, min(value, maximum))


def is_positive(value: float) -> bool:
    """Return True when ``value`` is strictly greater than zero."""
    return value > 0


def is_non_negative(value: float) -> bool:
    """Return True when ``value`` is greater than or equal to zero."""
    return value >= 0


# ── Strings ───────────────────────────────────────────────────────────────────

def is_non_empty_string(value: str) -> bool:
    """Return True when ``value`` is a non-empty, non-whitespace string."""
    return isinstance(value, str) and bool(value.strip())


def sanitise_string(value: str, max_length: int = 255) -> str:
    """
    Strip leading/trailing whitespace and truncate to ``max_length``.

    Parameters
    ----------
    value:
        The raw string to sanitise.
    max_length:
        Maximum allowed length after stripping.
    """
    cleaned = value.strip()
    return cleaned[:max_length]
