"""
Liquidity Engine.

Evaluates current liquidity level, growth trend, and stability.

Preferred liquidity range: $10K – $1M USD.
Liquidity stability (low variance) rewards patient capital.

max_score: 25
"""

from __future__ import annotations

import logging
from statistics import mean, stdev
from typing import Any, Optional

from app.analysis.models import ScoreResult

logger = logging.getLogger(__name__)

MAX_SCORE: float = 25.0


def evaluate(
    current_liq: Optional[float],
    history: list[dict[str, Any]],
) -> ScoreResult:
    """
    Evaluate liquidity quality.

    Parameters
    ----------
    current_liq:
        Most recent liquidity in USD.
    history:
        History rows newest-first (liquidity_usd field used).
    """
    if current_liq is None or current_liq <= 0:
        return ScoreResult(
            score=0.0,
            max_score=MAX_SCORE,
            reason="No liquidity data",
            details={"current_liq": None},
        )

    # 1. Current liquidity score (0-10)
    liq_score = _current_liq_score(current_liq) * 10.0

    # 2. Liquidity growth score from history (0-8)
    liq_values = [
        r.get("liquidity_usd")
        for r in reversed(history)   # chronological
        if r.get("liquidity_usd") is not None and r.get("liquidity_usd") > 0
    ]
    growth_score = _growth_score(liq_values) * 8.0

    # 3. Stability score (0-7): low std-dev of % changes = stable = good
    stability_score = _stability_score(liq_values) * 7.0

    total = min(MAX_SCORE, liq_score + growth_score + stability_score)

    details: dict[str, Any] = {
        "current_liq_usd": round(current_liq, 2),
        "liq_score": round(liq_score, 2),
        "growth_score": round(growth_score, 2),
        "stability_score": round(stability_score, 2),
        "history_points": len(liq_values),
    }

    reason = _build_reason(current_liq, growth_score, stability_score)
    return ScoreResult(score=round(total, 2), max_score=MAX_SCORE, reason=reason, details=details)


def _current_liq_score(liq: float) -> float:
    """Return 0-1 score based on current liquidity magnitude."""
    if liq >= 10_000 and liq <= 1_000_000:
        return 1.0
    if liq >= 5_000 and liq < 10_000:
        return 0.7
    if liq > 1_000_000 and liq <= 5_000_000:
        return 0.8
    if liq > 5_000_000:
        return 0.6   # Possibly a whale pool, less upside
    if liq >= 1_000:
        return 0.4
    return 0.1


def _growth_score(values: list[float]) -> float:
    """Return 0-1 score for growth trend in historical liquidity values."""
    if len(values) < 2:
        return 0.4   # Partial credit for unknown history

    first, last = values[0], values[-1]
    if first <= 0:
        return 0.4

    overall_pct = (last - first) / first * 100
    if overall_pct >= 20:
        return 1.0
    if overall_pct >= 5:
        return 0.8
    if overall_pct >= 0:
        return 0.6
    if overall_pct >= -15:
        return 0.3
    return 0.0


def _stability_score(values: list[float]) -> float:
    """Return 0-1 score: lower standard deviation in % changes = more stable."""
    if len(values) < 2:
        return 0.5

    pct_changes: list[float] = []
    for i in range(1, len(values)):
        if values[i - 1] > 0:
            pct_changes.append((values[i] - values[i - 1]) / values[i - 1] * 100)

    if not pct_changes:
        return 0.5

    try:
        sd = stdev(pct_changes) if len(pct_changes) > 1 else abs(pct_changes[0])
    except Exception:
        return 0.5

    if sd < 5:
        return 1.0
    if sd < 15:
        return 0.8
    if sd < 30:
        return 0.6
    if sd < 60:
        return 0.3
    return 0.1


def _build_reason(liq: float, growth: float, stability: float) -> str:
    if liq >= 100_000:
        liq_desc = f"strong liquidity ${liq / 1000:.0f}K"
    elif liq >= 10_000:
        liq_desc = f"adequate liquidity ${liq / 1000:.0f}K"
    else:
        liq_desc = f"low liquidity ${liq:.0f}"

    trend = "growing" if growth >= 5.6 else ("stable" if growth >= 4.0 else "declining")
    stability_desc = "stable" if stability >= 4.9 else "volatile"
    return f"{liq_desc}, {trend} and {stability_desc}"
