"""
Sprint 3 analysis domain models.

Shared data structures used by every scoring engine, the alert engine,
ranking, and milestone tracking. No business logic lives here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ── Risk Level ────────────────────────────────────────────────────────────────

class RiskLevel(str, Enum):
    """Overall risk rating produced by the Risk Engine."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"

    @property
    def sort_order(self) -> int:
        """Lower number = safer."""
        return {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3, "UNKNOWN": 4}[
            self.value
        ]

    def is_acceptable(self, max_risk: "RiskLevel") -> bool:
        """Return True when this risk level is within the allowed maximum."""
        return self.sort_order <= max_risk.sort_order

    @classmethod
    def from_string(cls, value: str) -> "RiskLevel":
        try:
            return cls(value.upper())
        except ValueError:
            return cls.UNKNOWN


# ── Market Mode ───────────────────────────────────────────────────────────────

class MarketMode(str, Enum):
    """Aggregate market sentiment derived from active token metrics."""

    BULL = "BULL"
    NEUTRAL = "NEUTRAL"
    WEAK = "WEAK"

    @classmethod
    def from_string(cls, value: str) -> "MarketMode":
        try:
            return cls(value.upper())
        except ValueError:
            return cls.NEUTRAL


# ── Score Result ──────────────────────────────────────────────────────────────

@dataclass
class ScoreResult:
    """
    Output from a single scoring engine.

    Attributes
    ----------
    score:
        Achieved score.  Always in [0, max_score].
    max_score:
        Maximum achievable score for this engine.
    reason:
        One-line human-readable explanation (must originate from real data).
    details:
        Engine-specific breakdown for debugging and logging.
    """

    score: float
    max_score: float
    reason: str
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def normalized(self) -> float:
        """Normalised score in [0, 1]."""
        if self.max_score <= 0:
            return 0.0
        return max(0.0, min(1.0, self.score / self.max_score))


# ── Evaluation Result ─────────────────────────────────────────────────────────

@dataclass
class EvaluationResult:
    """
    Complete evaluation of one token at one point in time.

    Produced by :class:`~app.analysis.scorer.ScoringEngine` and persisted
    to the ``evaluations`` table.
    """

    mint: str
    final_score: float           # 0–100, weighted normalised
    confidence: float            # 0–100 percentage
    risk_level: RiskLevel
    market_mode: MarketMode
    reasons: list[str]           # One entry per engine that contributed
    engine_scores: dict[str, ScoreResult]
    scan_count: int
    eligible_for_alert: bool
    evaluated_at: str            # ISO 8601 UTC

    def to_db_row(self) -> dict[str, Any]:
        """Serialise for database storage."""
        return {
            "mint": self.mint,
            "score": round(self.final_score, 2),
            "max_score": 100.0,
            "confidence": round(self.confidence, 2),
            "risk_level": self.risk_level.value,
            "reasons": json.dumps(self.reasons),
            "details": json.dumps({
                k: {
                    "score": round(v.score, 3),
                    "max_score": round(v.max_score, 3),
                    "reason": v.reason,
                }
                for k, v in self.engine_scores.items()
            }),
            "market_mode": self.market_mode.value,
            "scan_count": self.scan_count,
            "evaluated_at": self.evaluated_at,
        }


# ── Milestone Kind ────────────────────────────────────────────────────────────

class MilestoneKind(str, Enum):
    """Supported milestone event types."""

    GAIN_25 = "GAIN_25"
    GAIN_50 = "GAIN_50"
    GAIN_100 = "GAIN_100"
    GAIN_200 = "GAIN_200"
    GAIN_500 = "GAIN_500"
    GAIN_1000 = "GAIN_1000"
    ATH = "ATH"
    DRAWDOWN_30 = "DRAWDOWN_30"
    DRAWDOWN_50 = "DRAWDOWN_50"
    RUG = "RUG"
    DEATH = "DEATH"

    @property
    def label(self) -> str:
        labels = {
            "GAIN_25": "+25%",
            "GAIN_50": "+50%",
            "GAIN_100": "+100% 🎯",
            "GAIN_200": "+200% 🚀",
            "GAIN_500": "+500% 🔥",
            "GAIN_1000": "+1000% 💎",
            "ATH": "All-Time High",
            "DRAWDOWN_30": "-30% from ATH",
            "DRAWDOWN_50": "-50% from ATH",
            "RUG": "Rug Detected ⚠️",
            "DEATH": "Token Death 💀",
        }
        return labels.get(self.value, self.value)
