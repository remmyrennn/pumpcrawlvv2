"""
Market Cap Engine.

Preferred range: $25K – $300K USD.
Tokens outside this range are NOT instantly rejected — the score degrades
gradually based on distance from the preferred zone.

max_score: 10
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.analysis.models import ScoreResult

logger = logging.getLogger(__name__)

MAX_SCORE: float = 10.0

# Preferred zone
_MC_IDEAL_LOW: float = 25_000.0
_MC_IDEAL_HIGH: float = 300_000.0


def evaluate(market_cap_usd: Optional[float]) -> ScoreResult:
    """
    Evaluate token based on current market cap.

    Parameters
    ----------
    market_cap_usd:
        Current market cap in USD.  None → score 0.

    Returns
    -------
    ScoreResult
    """
    if market_cap_usd is None or market_cap_usd <= 0:
        return ScoreResult(
            score=0.0,
            max_score=MAX_SCORE,
            reason="No market cap data",
            details={"market_cap_usd": None},
        )

    ratio = _mc_ratio(market_cap_usd)
    score = ratio * MAX_SCORE

    details: dict[str, Any] = {
        "market_cap_usd": round(market_cap_usd, 2),
        "ideal_low": _MC_IDEAL_LOW,
        "ideal_high": _MC_IDEAL_HIGH,
        "ratio": round(ratio, 3),
    }

    reason = _build_reason(market_cap_usd, ratio)
    return ScoreResult(score=round(score, 2), max_score=MAX_SCORE, reason=reason, details=details)


def _mc_ratio(mc: float) -> float:
    """Return 0-1 score ratio for market cap placement."""
    # In preferred zone
    if _MC_IDEAL_LOW <= mc <= _MC_IDEAL_HIGH:
        return 1.0

    # Below preferred zone
    if mc < _MC_IDEAL_LOW:
        if mc >= 10_000:
            return 0.7    # Close below — promising micro-cap
        if mc >= 5_000:
            return 0.5
        if mc >= 1_000:
            return 0.3
        return 0.1        # Dust cap

    # Above preferred zone
    if mc <= 1_000_000:
        return 0.8        # Good size, slightly above preferred
    if mc <= 5_000_000:
        return 0.6
    if mc <= 20_000_000:
        return 0.4
    if mc <= 100_000_000:
        return 0.25
    return 0.1            # Very large — less upside potential


def _build_reason(mc: float, ratio: float) -> str:
    if mc >= 1_000_000:
        mc_str = f"${mc / 1_000_000:.2f}M"
    elif mc >= 1_000:
        mc_str = f"${mc / 1_000:.0f}K"
    else:
        mc_str = f"${mc:.0f}"

    if ratio >= 0.9:
        zone = "in ideal range"
    elif ratio >= 0.6:
        zone = "near ideal range"
    elif ratio >= 0.3:
        zone = "outside preferred range"
    else:
        zone = "far outside preferred range"

    return f"Market cap {mc_str} — {zone} ($25K–$300K preferred)"
