"""
Confidence Engine.

Confidence is INDEPENDENT from the final conviction score.

It measures how much we trust the evaluation, based on:
- Number of rescans (more history = higher confidence)
- Score consistency across history (stable evaluations = reliable)
- Risk level (low risk = more trustworthy signal)
- Data completeness (all fields populated = reliable)
- Trend agreement (all indicators pointing same direction)

Returns a percentage in [0, 100].  NOT a ScoreResult — confidence is
its own dimension and never contributes to final_score weighting.
"""

from __future__ import annotations

import logging
from statistics import mean, stdev
from typing import Any, Optional

from app.analysis.models import RiskLevel

logger = logging.getLogger(__name__)


def calculate(
    scan_count: int,
    risk_level: RiskLevel,
    historical_scores: list[float],
    current_token_data_complete: bool,
    trend_score_normalized: float,
    buy_pressure_normalized: float,
) -> float:
    """
    Compute confidence percentage (0–100).

    Parameters
    ----------
    scan_count:
        Number of times the token has been scanned.
    risk_level:
        Current risk classification.
    historical_scores:
        Previous final_score values from the evaluations table (newest-first).
    current_token_data_complete:
        True when all core token fields are non-None.
    trend_score_normalized:
        Trend engine normalised score (0–1).
    buy_pressure_normalized:
        Buy pressure engine normalised score (0–1).
    """
    confidence = 20.0  # Base confidence

    # ── Rescan depth (max +30) ────────────────────────────────────────────
    # Each additional scan above 1 adds confidence (diminishing returns)
    if scan_count >= 10:
        confidence += 30.0
    elif scan_count >= 5:
        confidence += 20.0
    elif scan_count >= 3:
        confidence += 12.0
    elif scan_count >= 2:
        confidence += 6.0

    # ── Historical score agreement (max +20) ──────────────────────────────
    # Low variance across past evaluations = reliable signal
    if len(historical_scores) >= 3:
        try:
            sd = stdev(historical_scores)
            if sd < 5:
                confidence += 20.0
            elif sd < 10:
                confidence += 14.0
            elif sd < 20:
                confidence += 8.0
            elif sd < 30:
                confidence += 3.0
        except Exception:
            pass
    elif len(historical_scores) >= 2:
        diff = abs(historical_scores[0] - historical_scores[1])
        if diff < 10:
            confidence += 10.0
        elif diff < 20:
            confidence += 5.0

    # ── Risk bonus (max +15) ──────────────────────────────────────────────
    risk_bonuses = {
        RiskLevel.LOW: 15.0,
        RiskLevel.MEDIUM: 8.0,
        RiskLevel.HIGH: 2.0,
        RiskLevel.CRITICAL: 0.0,
        RiskLevel.UNKNOWN: 4.0,
    }
    confidence += risk_bonuses.get(risk_level, 0.0)

    # ── Data quality (max +10) ────────────────────────────────────────────
    if current_token_data_complete:
        confidence += 10.0
    else:
        confidence += 3.0

    # ── Trend agreement (max +5) ──────────────────────────────────────────
    # When trend and buy pressure both point up, signal is more reliable
    if trend_score_normalized >= 0.6 and buy_pressure_normalized >= 0.6:
        confidence += 5.0
    elif trend_score_normalized >= 0.4 and buy_pressure_normalized >= 0.4:
        confidence += 2.0

    return round(min(100.0, max(0.0, confidence)), 2)
