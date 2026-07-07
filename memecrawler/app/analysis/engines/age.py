"""
Age Engine.

Evaluates token maturity based on how long the token/pair has existed.

Sweet spot: 24h–168h (1 day to 1 week).
No hard cutoffs — score degrades gradually outside this zone.

max_score: 10
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.analysis.models import ScoreResult

logger = logging.getLogger(__name__)

MAX_SCORE: float = 10.0

# Boundaries in hours
_TOO_NEW_H: float = 1.0
_SWEET_LOW_H: float = 24.0
_SWEET_HIGH_H: float = 168.0   # 1 week
_OLD_H: float = 720.0          # 1 month


def evaluate(age_seconds: Optional[float]) -> ScoreResult:
    """
    Evaluate token maturity.

    Parameters
    ----------
    age_seconds:
        Age of the token pair in seconds.  None → score 0.

    Returns
    -------
    ScoreResult
    """
    if age_seconds is None or age_seconds < 0:
        return ScoreResult(
            score=0.0,
            max_score=MAX_SCORE,
            reason="Token age unknown",
            details={"age_seconds": None},
        )

    age_hours = age_seconds / 3600.0
    ratio = _age_ratio(age_hours)
    score = ratio * MAX_SCORE

    details: dict[str, Any] = {
        "age_seconds": round(age_seconds, 0),
        "age_hours": round(age_hours, 2),
        "ratio": round(ratio, 3),
    }

    reason = _build_reason(age_hours, ratio)
    return ScoreResult(score=round(score, 2), max_score=MAX_SCORE, reason=reason, details=details)


def _age_ratio(age_hours: float) -> float:
    """Map token age (hours) to a 0-1 score."""
    if age_hours < 0.25:
        return 0.1     # Extremely new — too risky
    if age_hours < _TOO_NEW_H:
        return 0.3
    if age_hours < 6:
        return 0.5     # Still very new
    if age_hours < _SWEET_LOW_H:
        return 0.7     # Approaching sweet spot
    if age_hours <= _SWEET_HIGH_H:
        return 1.0     # Sweet spot
    if age_hours <= _OLD_H:
        return 0.8     # Established but less upside
    if age_hours <= 2160:  # 3 months
        return 0.6
    return 0.4         # Very old


def _build_reason(age_hours: float, ratio: float) -> str:
    if age_hours < 1:
        desc = f"{age_hours * 60:.0f}m old — very new"
    elif age_hours < 24:
        desc = f"{age_hours:.0f}h old — new"
    elif age_hours < 168:
        d = age_hours / 24
        desc = f"{d:.1f}d old — in sweet spot"
    elif age_hours < 720:
        d = age_hours / 24
        desc = f"{d:.0f}d old — established"
    else:
        d = age_hours / 24
        desc = f"{d:.0f}d old — mature"

    zone = "ideal" if ratio >= 0.9 else ("good" if ratio >= 0.65 else "suboptimal")
    return f"Token {desc} ({zone} age zone)"
