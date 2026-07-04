"""
Buy Pressure Engine.

Evaluates buy/sell ratio, momentum (improving ratio over time), and
historical improvement in buyer activity.

A buy ratio > 0.6 (60%+ of transactions are buys) indicates positive pressure.

max_score: 20
"""

from __future__ import annotations

import logging
from statistics import mean
from typing import Any, Optional

from app.analysis.models import ScoreResult

logger = logging.getLogger(__name__)

MAX_SCORE: float = 20.0


def evaluate(
    current_buys: Optional[int],
    current_sells: Optional[int],
    history: list[dict[str, Any]],
) -> ScoreResult:
    """
    Evaluate buy pressure strength.

    Parameters
    ----------
    current_buys:
        Most recent 5-minute buy count.
    current_sells:
        Most recent 5-minute sell count.
    history:
        History rows newest-first (buys_5m, sells_5m fields used).
    """
    # 1. Current buy/sell ratio (0-8)
    ratio_score, current_ratio = _ratio_score(current_buys, current_sells)
    ratio_score *= 8.0

    # 2. Momentum — is the ratio improving? (0-6)
    rows_chrono = list(reversed(history))
    ratios_over_time = _historical_ratios(rows_chrono)
    momentum_score = _momentum_score(ratios_over_time, current_ratio) * 6.0

    # 3. Historical buy improvement (0-6)
    buy_history = [
        r.get("buys_5m")
        for r in rows_chrono
        if r.get("buys_5m") is not None and r.get("buys_5m") >= 0
    ]
    improvement_score = _improvement_score(buy_history) * 6.0

    total = min(MAX_SCORE, ratio_score + momentum_score + improvement_score)

    details: dict[str, Any] = {
        "current_buys": current_buys,
        "current_sells": current_sells,
        "current_ratio": round(current_ratio, 3) if current_ratio is not None else None,
        "ratio_score": round(ratio_score, 2),
        "momentum_score": round(momentum_score, 2),
        "improvement_score": round(improvement_score, 2),
        "history_ratios": len(ratios_over_time),
    }

    reason = _build_reason(current_ratio, ratio_score, momentum_score)
    return ScoreResult(score=round(total, 2), max_score=MAX_SCORE, reason=reason, details=details)


def _ratio_score(buys: Optional[int], sells: Optional[int]) -> tuple[float, Optional[float]]:
    """Return (0-1 score, ratio) for current buy/sell activity."""
    if buys is None and sells is None:
        return 0.4, None  # Unknown — partial credit

    b = buys or 0
    s = sells or 0
    total = b + s

    if total == 0:
        return 0.3, None   # No activity

    ratio = b / total

    if ratio >= 0.75:
        return 1.0, ratio
    if ratio >= 0.60:
        return 0.85, ratio
    if ratio >= 0.50:
        return 0.65, ratio
    if ratio >= 0.40:
        return 0.45, ratio
    if ratio >= 0.30:
        return 0.25, ratio
    return 0.1, ratio


def _historical_ratios(rows_chrono: list[dict]) -> list[float]:
    """Extract buy ratios from history in chronological order."""
    ratios = []
    for row in rows_chrono:
        b = row.get("buys_5m")
        s = row.get("sells_5m")
        if b is None or s is None:
            continue
        total = (b or 0) + (s or 0)
        if total > 0:
            ratios.append((b or 0) / total)
    return ratios


def _momentum_score(ratios: list[float], current_ratio: Optional[float]) -> float:
    """Return 0-1 score based on whether buy ratio is improving over time."""
    all_ratios = ratios + ([current_ratio] if current_ratio is not None else [])
    if len(all_ratios) < 2:
        return 0.4

    first_half = all_ratios[:len(all_ratios) // 2]
    second_half = all_ratios[len(all_ratios) // 2:]

    avg_first = mean(first_half) if first_half else 0.5
    avg_second = mean(second_half) if second_half else 0.5

    delta = avg_second - avg_first
    if delta >= 0.15:
        return 1.0   # Strong improvement
    if delta >= 0.05:
        return 0.8
    if delta >= -0.05:
        return 0.5   # Flat
    if delta >= -0.15:
        return 0.3
    return 0.0       # Deteriorating


def _improvement_score(buy_counts: list[int]) -> float:
    """Return 0-1 score for trend in absolute buy counts."""
    if len(buy_counts) < 2:
        return 0.4

    first, last = buy_counts[0], buy_counts[-1]
    if first == 0:
        return 0.5 if last > 0 else 0.3

    pct_change = (last - first) / first * 100
    if pct_change >= 50:
        return 1.0
    if pct_change >= 20:
        return 0.8
    if pct_change >= 0:
        return 0.6
    if pct_change >= -20:
        return 0.3
    return 0.0


def _build_reason(
    ratio: Optional[float],
    ratio_score: float,
    momentum_score: float,
) -> str:
    if ratio is None:
        return "No transaction data for buy pressure analysis"

    pct = round(ratio * 100)
    if ratio >= 0.65:
        pressure = "strong buy pressure"
    elif ratio >= 0.50:
        pressure = "moderate buy pressure"
    else:
        pressure = "sell-dominant"

    trend = "improving" if momentum_score >= 4.2 else ("stable" if momentum_score >= 3.0 else "weakening")
    return f"{pct}% buys — {pressure}, {trend} momentum"
