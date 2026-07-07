"""
Stability Engine.

Evaluates price and liquidity stability over historical scans.
Measures standard deviation of percentage changes; rewards low volatility
and recovery after drawdowns.

max_score: 15
"""

from __future__ import annotations

import logging
from statistics import mean, stdev
from typing import Any, Optional

from app.analysis.models import ScoreResult

logger = logging.getLogger(__name__)

MAX_SCORE: float = 15.0


def evaluate(
    history: list[dict[str, Any]],
) -> ScoreResult:
    """
    Evaluate market stability from historical data.

    Parameters
    ----------
    history:
        History rows newest-first (market_cap_usd, liquidity_usd used).
    """
    rows_chrono = list(reversed(history))

    mc_values = [
        r.get("market_cap_usd")
        for r in rows_chrono
        if r.get("market_cap_usd") is not None and r.get("market_cap_usd") > 0
    ]
    liq_values = [
        r.get("liquidity_usd")
        for r in rows_chrono
        if r.get("liquidity_usd") is not None and r.get("liquidity_usd") > 0
    ]

    if not mc_values and not liq_values:
        return ScoreResult(
            score=MAX_SCORE * 0.4,
            max_score=MAX_SCORE,
            reason="Insufficient data for stability analysis",
            details={"records": 0},
        )

    # 1. MC stability (0-6)
    mc_stability = _stability_score(mc_values) * 6.0

    # 2. Liquidity stability (0-6)
    liq_stability = _stability_score(liq_values) * 6.0

    # 3. Recovery behaviour (0-3): does price recover after dips?
    recovery_score = _recovery_score(mc_values) * 3.0

    total = min(MAX_SCORE, mc_stability + liq_stability + recovery_score)

    mc_sd = _pct_stdev(mc_values)
    liq_sd = _pct_stdev(liq_values)

    details: dict[str, Any] = {
        "mc_pct_stdev": round(mc_sd, 2) if mc_sd is not None else None,
        "liq_pct_stdev": round(liq_sd, 2) if liq_sd is not None else None,
        "mc_stability_score": round(mc_stability, 2),
        "liq_stability_score": round(liq_stability, 2),
        "recovery_score": round(recovery_score, 2),
        "mc_records": len(mc_values),
        "liq_records": len(liq_values),
    }

    reason = _build_reason(mc_stability, liq_stability, mc_sd)
    return ScoreResult(score=round(total, 2), max_score=MAX_SCORE, reason=reason, details=details)


def _pct_changes(values: list[float]) -> list[float]:
    changes = []
    for i in range(1, len(values)):
        if values[i - 1] > 0:
            changes.append((values[i] - values[i - 1]) / values[i - 1] * 100)
    return changes


def _pct_stdev(values: list[float]) -> Optional[float]:
    changes = _pct_changes(values)
    if len(changes) < 2:
        return None
    try:
        return stdev(changes)
    except Exception:
        return None


def _stability_score(values: list[float]) -> float:
    """Return 0-1 stability score from a series of metric values."""
    if len(values) < 2:
        return 0.5   # Unknown — partial credit

    sd = _pct_stdev(values)
    if sd is None:
        return 0.5

    if sd < 5:
        return 1.0   # Very stable
    if sd < 15:
        return 0.85
    if sd < 30:
        return 0.65
    if sd < 60:
        return 0.40
    if sd < 100:
        return 0.20
    return 0.05      # Extremely volatile


def _recovery_score(mc_values: list[float]) -> float:
    """
    Return 0-1 score for recovery behaviour.

    Looks for dips (>20% drop) followed by recovery (>50% of drop
    recovered in subsequent periods).
    """
    if len(mc_values) < 4:
        return 0.5   # Not enough data

    changes = _pct_changes(mc_values)
    if not changes:
        return 0.5

    dips_found = 0
    recoveries = 0

    for i, ch in enumerate(changes):
        if ch <= -20:
            dips_found += 1
            # Check if next change is positive (recovery)
            if i + 1 < len(changes) and changes[i + 1] > 0:
                # Recovery: positive follow-up after significant dip
                recovery_pct = changes[i + 1] / abs(ch)
                if recovery_pct >= 0.5:
                    recoveries += 1

    if dips_found == 0:
        return 0.7   # No dips — stable; partial recovery credit

    return recoveries / dips_found


def _build_reason(mc_stab: float, liq_stab: float, mc_sd: Optional[float]) -> str:
    total_stab = mc_stab + liq_stab
    if total_stab >= 10:
        quality = "highly stable"
    elif total_stab >= 7:
        quality = "moderately stable"
    elif total_stab >= 4:
        quality = "somewhat volatile"
    else:
        quality = "highly volatile"

    if mc_sd is not None:
        sd_str = f" (MC σ={mc_sd:.0f}%)"
    else:
        sd_str = ""

    return f"Token is {quality}{sd_str}"
