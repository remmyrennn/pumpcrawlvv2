"""
Formatting utilities.

Centralises number, currency, percentage, and address formatting so that
output is consistent across every module.
"""

from __future__ import annotations

import math


def format_number(value: float, decimals: int = 2) -> str:
    """
    Format a number with thousand separators and fixed decimal places.

    Examples
    --------
    >>> format_number(1_234_567.89)
    '1,234,567.89'
    >>> format_number(0.000123, decimals=6)
    '0.000123'
    """
    return f"{value:,.{decimals}f}"


def format_usd(value: float) -> str:
    """
    Format a value as US dollars.

    Adapts decimal precision to the magnitude of the value so that very
    small prices (e.g. micro-cap meme coins) remain readable.

    Examples
    --------
    >>> format_usd(1_234.5)
    '$1,234.50'
    >>> format_usd(0.0000043)
    '$0.0000043'
    """
    if value == 0:
        return "$0.00"
    abs_val = abs(value)
    if abs_val >= 1:
        return f"${value:,.2f}"
    # Find how many leading zeros after the decimal point
    leading_zeros = max(0, -math.floor(math.log10(abs_val)))
    decimals = leading_zeros + 3
    return f"${value:.{decimals}f}"


def format_percentage(value: float, decimals: int = 2) -> str:
    """
    Format a ratio (0–1) or a percentage value as a percentage string.

    Parameters
    ----------
    value:
        The value to format. Values > 1 are assumed to already be a
        percentage (e.g. 45.5 → "45.50%"). Values ≤ 1 are multiplied by
        100 (e.g. 0.455 → "45.50%").
    decimals:
        Number of decimal places.
    """
    pct = value if abs(value) > 1 else value * 100
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.{decimals}f}%"


def format_market_cap(value: float) -> str:
    """
    Format a market cap value with a human-readable suffix.

    Examples
    --------
    >>> format_market_cap(1_500_000)
    '$1.50M'
    >>> format_market_cap(345_000_000_000)
    '$345.00B'
    """
    tiers = [
        (1e12, "T"),
        (1e9, "B"),
        (1e6, "M"),
        (1e3, "K"),
    ]
    for threshold, suffix in tiers:
        if abs(value) >= threshold:
            return f"${value / threshold:.2f}{suffix}"
    return format_usd(value)


def format_volume(value: float) -> str:
    """
    Format a 24-hour trading volume with a human-readable suffix.

    Delegates to :func:`format_market_cap` since the formatting rules
    are identical.
    """
    return format_market_cap(value)


def truncate_address(address: str, chars: int = 6) -> str:
    """
    Truncate a Solana public key for display.

    Examples
    --------
    >>> truncate_address("So11111111111111111111111111111112", chars=6)
    'So1111...1112'
    """
    if len(address) <= chars * 2:
        return address
    return f"{address[:chars]}...{address[-chars:]}"


def format_token_age(seconds: float) -> str:
    """
    Format a token age (in seconds) as a human-readable string.

    Examples
    --------
    >>> format_token_age(3700)
    '1h 1m'
    >>> format_token_age(90000)
    '1d 1h'
    """
    seconds = int(seconds)
    days, remainder = divmod(seconds, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes, _ = divmod(remainder, 60)

    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def escape_markdown(text: str) -> str:
    """
    Escape special characters for Telegram MarkdownV2 messages.

    Telegram MarkdownV2 requires escaping: _ * [ ] ( ) ~ ` > # + - = | { } . !
    """
    special_chars = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special_chars else c for c in text)
