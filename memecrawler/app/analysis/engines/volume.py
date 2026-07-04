"""
Volume Engine.

Evaluates trading volume for organic activity, growth, consistency,
and the absence of suspicious single-cycle spikes.

A healthy vol/MC ratio is 0.5–5.0 (50%–500% daily volume relative to cap).
Below 0.1 indicates dead trading; above 10 can signal wash trading.

max_score: 25
"""

from __future__ import annotations

import logging
from statistics import mean, stdev
from typing import Any, Optional

from app.analysis.models import ScoreResult

logger = logging.getLogger(__name__)

MAX_SCORE: float = 25.0

_ORGANIC_RATIO_LOW: float = 0.5
_ORGANIC_RATIO_HIGH: float = 5.0


def evaluate(
    current_vol: Optional[float],
    current_mc: Optional[float],
    history: list[dict[str, Any]],
) -> ScoreResult:
    """
    Evaluate volume quality.

    Parameters
    ----------
    current_vol:
        Most recent 24h volume in USD.
    current_mc:
        Most recent market cap in USD.
    history:
        History rows newest-first.
    """
    if current_vol is None or current_vol <= 0:
        return ScoreResult(
            score=0.0,
            max_score=MAX_SCORE,
            reason="No volume data",
            details={"current_vol": None},
        )

    # 1. Vol/MC ratio score (0-10)
    ratio_score = _ratio_score(current_vol, current_mc) * 10.0

    # 2. Volume growth score (0-8)
    vol_history = [
        r.get("volume_24h_usd")
        for r in reversed(history)
        if r.get("volume_24h_usd") is not None and r.get("volume_24h_usd") > 0
    ]
    growth_score = _growth_score(vol_history) * 8.0

    # 3. Consistency score — penalise single-cycle spikes (0-7)
    consistency_score = _consistency_score(vol_history) * 7.0

    total = min(MAX_SCORE, ratio_score + growth_score + consistency_score)

    vol_mc_ratio = (current_vol / current_mc) if current_mc and current_mc > 0 else None
    details: dict[str, Any] = {
        "current_vol_usd": round(current_vol, 2),
        "vol_mc_ratio": round(vol_mc_ratio, 4) if vol_mc_ratio is not None else None,
        "ratio_score": round(ratio_score, 2),
        "growth_score": round(growth_score, 2),
        "consistency_score": round(consistency_score, 2),
        "suspicious_spike": _has_suspicious_spike(vol_history),
    }

    reason = _build_reason(current_vol, vol_mc_ratio, ratio_score + growth_score)
    return ScoreResult(score=round(total, 2), max_score=MAX_SCORE, reason=reason, details=details)


def _ratio_score(vol: float, mc: Optional[float]) -> float:
    """Return 0-1 score for vol/MC ratio."""
    if mc is None or mc <= 0:
        # No MC data — score only on raw volume
        if vol >= 50_000:
            return 0.7
        if vol >= 10_000:
            return 0.5
        return 0.2

    ratio = vol / mc
    if _ORGANIC_RATIO_LOW <= ratio <= _ORGANIC_RATIO_HIGH:
        return 1.0
    if ratio < _ORGANIC_RATIO_LOW:
        if ratio >= 0.1:
            return 0.6
        if ratio >= 0.01:
            return 0.3
        return 0.0
    # Above _ORGANIC_RATIO_HIGH — potential wash trading
    if ratio <= 10:
        return 0.7
    if ratio <= 20:
        return 0.4
    return 0.1   # Extremely high vol/MC → suspicious


def _growth_score(values: list[float]) -> float:
    if len(values) < 2:
        return 0.4
    first, last = values[0], values[-1]
    if first <= 0:
        return 0.4
    pct = (last - first) / first * 100
    if pct >= 30:
        return 1.0
    if pct >= 10:
        return 0.8
    if pct >= 0:
        return 0.6
    if pct >= -20:
        return 0.3
    return 0.0


def _consistency_score(values: list[float]) -> float:
    """Return 0-1 score.  Penalise suspicious single-cycle spikes."""
    if len(values) < 2:
        return 0.5
    if _has_suspicious_spike(values):
        return 0.1

    try:
        sd = stdev(values) if len(values) > 1 else 0.0
        avg = mean(values)
        cv = sd / avg if avg > 0 else 0   # Coefficient of variation
    except Exception:
        return 0.5

    if cv < 0.3:
        return 1.0
    if cv < 0.6:
        return 0.8
    if cv < 1.0:
        return 0.6
    if cv < 2.0:
        return 0.4
    return 0.2


def _has_suspicious_spike(values: list[float]) -> bool:
    """True when a single value is >5x the average of the others."""
    if len(values) < 3:
        return False
    for i, v in enumerate(values):
        others = [x for j, x in enumerate(values) if j != i and x > 0]
        if not others:
            continue
        avg_others = mean(others)
        if avg_others > 0 and v / avg_others > 5:
            return True
    return False


def _build_reason(vol: float, ratio: Optional[float], sub_score: float) -> str:
    if vol >= 1_000_000:
        vol_str = f"${vol / 1_000_000:.1f}M vol"
    elif vol >= 1_000:
        vol_str = f"${vol / 1_000:.0f}K vol"
    else:
        vol_str = f"${vol:.0f} vol"

    if ratio is not None:
        ratio_str = f" ({ratio * 100:.0f}% vol/MC ratio)"
    else:
        ratio_str = ""

    quality = "organic" if sub_score >= 12 else ("moderate" if sub_score >= 7 else "weak")
    return f"{vol_str}{ratio_str} — {quality} volume activity"
