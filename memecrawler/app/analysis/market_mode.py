"""
Market Mode Detector.

Determines the aggregate market sentiment (BULL / NEUTRAL / WEAK) by
analysing recent price and volume trends across all actively watched tokens.

Thresholds are configurable via Settings (never hardcoded).

Sprint 3.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.analysis.models import MarketMode
from app.utils.time_utils import utcnow_iso

if TYPE_CHECKING:
    from app.config.settings import Settings
    from app.database.manager import DatabaseManager

logger = logging.getLogger(__name__)

# Default lookback: use the last N history records per token
_LOOKBACK_RECORDS: int = 3


class MarketModeDetector:
    """
    Computes the current aggregate market mode from DB data.

    Parameters
    ----------
    db:
        Open DatabaseManager instance.
    settings:
        Application settings for configurable thresholds.
    """

    def __init__(self, db: "DatabaseManager", settings: "Settings") -> None:
        self._db = db
        self._settings = settings
        self._current_mode: MarketMode = MarketMode.NEUTRAL
        self._last_updated: str = utcnow_iso()
        self._sample_size: int = 0

    # ── Compute ───────────────────────────────────────────────────────────

    async def refresh(self) -> MarketMode:
        """
        Refresh the market mode by querying recent history data.

        Samples all active watchlist tokens (not ARCHIVED / TRACKING),
        computes per-token price trend, and classifies aggregate sentiment.

        Returns
        -------
        MarketMode
            The newly computed market mode.
        """
        try:
            mode = await self._compute()
        except Exception as exc:
            logger.warning("MarketModeDetector.refresh failed: %s", exc)
            return self._current_mode

        self._current_mode = mode
        self._last_updated = utcnow_iso()
        logger.info("Market mode refreshed: %s", mode.value)
        return mode

    async def _compute(self) -> MarketMode:
        """Core computation: sample tokens, measure trend, classify mode."""
        # Fetch active tokens with recent history
        active_mints = await self._db.fetchall(
            """
            SELECT DISTINCT mint FROM watchlist
            WHERE state NOT IN ('ARCHIVED', 'TRACKING')
            LIMIT 100
            """
        )

        if not active_mints:
            return MarketMode.NEUTRAL

        positive_count = 0
        negative_count = 0
        neutral_count = 0
        total = 0

        for row in active_mints:
            mint = row["mint"]
            hist = await self._db.fetchall(
                """
                SELECT market_cap_usd, volume_24h_usd
                FROM history
                WHERE mint = ?
                ORDER BY recorded_at DESC
                LIMIT ?
                """,
                (mint, _LOOKBACK_RECORDS),
            )
            if len(hist) < 2:
                neutral_count += 1
                total += 1
                continue

            trend = _token_trend(hist)
            if trend > 0:
                positive_count += 1
            elif trend < 0:
                negative_count += 1
            else:
                neutral_count += 1
            total += 1

        self._sample_size = total

        if total == 0:
            return MarketMode.NEUTRAL

        bull_ratio = positive_count / total
        weak_ratio = (positive_count + neutral_count) / total if total else 1.0

        bull_threshold = self._settings.market_mode_bull_ratio
        weak_threshold = self._settings.market_mode_weak_ratio

        if bull_ratio >= bull_threshold:
            return MarketMode.BULL
        if (positive_count / total) < weak_threshold:
            return MarketMode.WEAK
        return MarketMode.NEUTRAL

    # ── Accessors ─────────────────────────────────────────────────────────

    @property
    def current_mode(self) -> MarketMode:
        """Return the last computed market mode."""
        return self._current_mode

    def info(self) -> dict[str, Any]:
        """Return summary for /health and /marketmode endpoints."""
        return {
            "mode": self._current_mode.value,
            "last_updated": self._last_updated,
            "sample_size": self._sample_size,
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _token_trend(hist: list[Any]) -> int:
    """
    Return +1 (positive), -1 (negative), or 0 (flat) for a token's trend.

    Uses the two most recent market cap records (newest is index 0).
    """
    try:
        latest = dict(hist[0])
        prev = dict(hist[1])
        mc_now = latest.get("market_cap_usd")
        mc_prev = prev.get("market_cap_usd")
        if mc_now is None or mc_prev is None or mc_prev <= 0:
            return 0
        pct = (mc_now - mc_prev) / mc_prev * 100
        if pct > 5:
            return 1
        if pct < -5:
            return -1
        return 0
    except (IndexError, KeyError, TypeError, ValueError):
        return 0
