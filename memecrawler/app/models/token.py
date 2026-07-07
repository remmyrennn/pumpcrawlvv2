"""
Token domain models.

Centralises all data structures used across the discovery, scanner,
watchlist, and alert pipeline so every module works with the same types.

No business logic lives here — this is a pure data layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


# ── State Machine ─────────────────────────────────────────────────────────────

class TokenState(str, Enum):
    """
    Deterministic state machine for a watched token.

    Allowed forward transitions
    ---------------------------
    NEW          → DISCOVERED, ARCHIVED
    DISCOVERED   → VALIDATED, ARCHIVED
    VALIDATED    → WATCHING, ARCHIVED
    WATCHING     → ACCUMULATING, ARCHIVED
    ACCUMULATING → HIGH_PRIORITY, WATCHING, ARCHIVED
    HIGH_PRIORITY → READY_FOR_ALERT, ACCUMULATING, ARCHIVED
    READY_FOR_ALERT → TRACKING, ARCHIVED
    TRACKING     → ARCHIVED
    """

    NEW = "NEW"
    DISCOVERED = "DISCOVERED"
    VALIDATED = "VALIDATED"
    WATCHING = "WATCHING"
    ACCUMULATING = "ACCUMULATING"
    HIGH_PRIORITY = "HIGH_PRIORITY"
    READY_FOR_ALERT = "READY_FOR_ALERT"
    TRACKING = "TRACKING"
    ARCHIVED = "ARCHIVED"


# Valid forward transitions (source → set of allowed targets)
_VALID_TRANSITIONS: dict[TokenState, frozenset[TokenState]] = {
    TokenState.NEW: frozenset({TokenState.DISCOVERED, TokenState.ARCHIVED}),
    TokenState.DISCOVERED: frozenset({TokenState.VALIDATED, TokenState.ARCHIVED}),
    TokenState.VALIDATED: frozenset({TokenState.WATCHING, TokenState.ARCHIVED}),
    TokenState.WATCHING: frozenset({TokenState.ACCUMULATING, TokenState.ARCHIVED}),
    TokenState.ACCUMULATING: frozenset({
        TokenState.HIGH_PRIORITY, TokenState.WATCHING, TokenState.ARCHIVED
    }),
    TokenState.HIGH_PRIORITY: frozenset({
        TokenState.READY_FOR_ALERT, TokenState.ACCUMULATING, TokenState.ARCHIVED
    }),
    TokenState.READY_FOR_ALERT: frozenset({TokenState.TRACKING, TokenState.ARCHIVED}),
    TokenState.TRACKING: frozenset({TokenState.ARCHIVED}),
    TokenState.ARCHIVED: frozenset(),  # terminal
}


def is_valid_transition(current: TokenState, target: TokenState) -> bool:
    """Return True when transitioning from *current* to *target* is permitted."""
    return target in _VALID_TRANSITIONS.get(current, frozenset())


# ── Scan Priority ─────────────────────────────────────────────────────────────

class ScanPriority(str, Enum):
    """
    Scan frequency tiers.

    Tier       Interval
    ------     --------
    LOW        every 300 s  (5 min)
    MEDIUM     every 120 s  (2 min)
    HIGH       every  60 s  (1 min)
    CRITICAL   every  20 s
    """

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @property
    def interval_seconds(self) -> int:
        """Seconds between scans for this priority tier."""
        return _PRIORITY_INTERVALS[self]

    @property
    def sort_order(self) -> int:
        """Lower number = higher urgency (scanned first in queue)."""
        return _PRIORITY_ORDER[self]


_PRIORITY_INTERVALS: dict[ScanPriority, int] = {
    ScanPriority.CRITICAL: 20,
    ScanPriority.HIGH: 60,
    ScanPriority.MEDIUM: 120,
    ScanPriority.LOW: 300,
}

_PRIORITY_ORDER: dict[ScanPriority, int] = {
    ScanPriority.CRITICAL: 0,
    ScanPriority.HIGH: 1,
    ScanPriority.MEDIUM: 2,
    ScanPriority.LOW: 3,
}


# ── Raw Provider Data ─────────────────────────────────────────────────────────

@dataclass
class TokenData:
    """
    Raw token market data as returned by a provider fetch.

    All numeric fields are ``Optional`` so that partial responses from
    a degraded provider can be stored without crashing the pipeline.
    A module must never assume any field is populated.
    """

    mint: str
    """Solana mint address (base-58 encoded)."""

    symbol: str = ""
    name: str = ""
    chain: str = "solana"

    price_usd: Optional[float] = None
    market_cap_usd: Optional[float] = None
    volume_24h_usd: Optional[float] = None
    liquidity_usd: Optional[float] = None

    buys_5m: Optional[int] = None
    sells_5m: Optional[int] = None
    buys_1h: Optional[int] = None
    sells_1h: Optional[int] = None

    age_seconds: Optional[float] = None
    """Seconds since the token/pair was first listed."""

    pair_address: Optional[str] = None
    """DEX pair address associated with this token."""

    provider: str = "unknown"
    """Name of the provider that fetched this data."""

    fetched_at: Optional[datetime] = None
    """UTC datetime when this data was retrieved."""


# ── Watchlist Entry ───────────────────────────────────────────────────────────

@dataclass
class WatchEntry:
    """
    A token currently under Watch Mode.

    Populated from the ``watchlist`` database table. All ISO-8601 timestamp
    fields are stored as strings so they can be serialised to/from SQLite
    without conversion overhead.
    """

    watch_id: str
    """Unique UUID assigned when the token was first added."""

    mint: str
    symbol: str
    name: str

    state: TokenState
    priority: ScanPriority

    reason_added: str
    """Human-readable reason the token was placed on the watchlist."""

    first_seen_at: str  # ISO 8601
    last_seen_at: str   # ISO 8601

    scan_count: int = 0
    last_scan_at: Optional[str] = None   # ISO 8601
    next_scan_at: Optional[str] = None   # ISO 8601

    # Latest market snapshot — updated after each scan
    price_usd: Optional[float] = None
    market_cap_usd: Optional[float] = None
    liquidity_usd: Optional[float] = None
    volume_24h_usd: Optional[float] = None

    # Sprint 3: scoring columns (populated after intelligence layer runs)
    score: Optional[float] = None
    confidence: Optional[float] = None
    risk_level: str = "UNKNOWN"
    alert_sent_at: Optional[str] = None
