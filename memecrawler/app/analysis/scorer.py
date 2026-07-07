"""
Scoring Engine.

Orchestrates all scoring sub-engines, applies weighted normalisation,
computes confidence, and produces a complete :class:`EvaluationResult`.

Weights are configurable via Settings.  The final score is always in
[0, 100] regardless of individual engine max-scores.

NEVER sums raw engine scores.  Every engine is normalised to [0, 1]
before weighting.

Sprint 3.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, TYPE_CHECKING

from app.analysis.engines import (
    age,
    buy_pressure,
    confidence as confidence_engine,
    liquidity,
    market_cap,
    risk,
    social,
    stability,
    trend,
    volume,
)
from app.analysis.models import EvaluationResult, MarketMode, RiskLevel, ScoreResult
from app.utils.time_utils import utcnow_iso

if TYPE_CHECKING:
    from app.config.settings import Settings
    from app.models.token import TokenData, WatchEntry
    from app.providers.rugcheck import RugCheckProvider

logger = logging.getLogger(__name__)

# ── Default engine weights — must sum to 1.0 ────────────────────────────────
_DEFAULT_WEIGHTS: dict[str, float] = {
    "trend":        0.25,
    "volume":       0.15,
    "buy_pressure": 0.15,
    "liquidity":    0.15,
    "market_cap":   0.10,
    "stability":    0.10,
    "age":          0.05,
    "social":       0.05,
}


class ScoringEngine:
    """
    Stateless weighted scoring engine.

    All state lives in the caller (WatchlistManager, DB).  The engine
    reads configuration once and applies it on every ``evaluate()`` call.

    Parameters
    ----------
    settings:
        Application configuration (thresholds, weights, feature flags).
    """

    def __init__(self, settings: "Settings") -> None:
        self._settings = settings
        self._weights = _build_weights(settings)

    async def evaluate(
        self,
        entry: "WatchEntry",
        current: "TokenData",
        history: list[dict[str, Any]],
        *,
        rugcheck_data: Optional[dict[str, Any]] = None,
        historical_scores: Optional[list[float]] = None,
        market_mode: MarketMode = MarketMode.NEUTRAL,
    ) -> EvaluationResult:
        """
        Produce a complete evaluation for one token at one point in time.

        Parameters
        ----------
        entry:
            Current watchlist entry (state, scan_count, etc.).
        current:
            Latest market data from provider.
        history:
            Historical scan records (newest-first) from the DB.
        rugcheck_data:
            Full RugCheck report dict (optional).
        historical_scores:
            List of previous final_score values (newest-first) from the
            evaluations table, used for confidence calculation.
        market_mode:
            Current aggregate market mode.
        """
        # ── Run all scoring engines ───────────────────────────────────────
        engine_scores: dict[str, ScoreResult] = {}

        engine_scores["trend"] = trend.evaluate(
            history=history,
            current_mc=current.market_cap_usd,
            current_liq=current.liquidity_usd,
            current_vol=current.volume_24h_usd,
        )

        engine_scores["liquidity"] = liquidity.evaluate(
            current_liq=current.liquidity_usd,
            history=history,
        )

        engine_scores["market_cap"] = market_cap.evaluate(
            market_cap_usd=current.market_cap_usd,
        )

        engine_scores["volume"] = volume.evaluate(
            current_vol=current.volume_24h_usd,
            current_mc=current.market_cap_usd,
            history=history,
        )

        engine_scores["buy_pressure"] = buy_pressure.evaluate(
            current_buys=current.buys_5m,
            current_sells=current.sells_5m,
            history=history,
        )

        engine_scores["stability"] = stability.evaluate(history=history)

        engine_scores["age"] = age.evaluate(age_seconds=current.age_seconds)

        engine_scores["social"] = social.evaluate(
            symbol=current.symbol or "",
            name=current.name or "",
            rugcheck_data=rugcheck_data,
        )

        # ── Risk engine (separate — affects eligibility, not weighted score)
        risk_level, risk_result = risk.evaluate(rugcheck_data=rugcheck_data)
        engine_scores["risk"] = risk_result

        # ── Weighted normalisation ────────────────────────────────────────
        final_score = _weighted_score(engine_scores, self._weights)

        # Apply market mode modifier
        final_score = _apply_market_mode(final_score, market_mode)

        # ── Confidence (independent) ──────────────────────────────────────
        data_complete = _data_complete(current)
        conf = confidence_engine.calculate(
            scan_count=entry.scan_count,
            risk_level=risk_level,
            historical_scores=historical_scores or [],
            current_token_data_complete=data_complete,
            trend_score_normalized=engine_scores["trend"].normalized,
            buy_pressure_normalized=engine_scores["buy_pressure"].normalized,
        )

        # ── Reasons (from each engine that contributed) ───────────────────
        reasons = _collect_reasons(engine_scores, risk_level, conf)

        # ── Alert eligibility ─────────────────────────────────────────────
        eligible = _is_eligible(
            final_score=final_score,
            confidence=conf,
            scan_count=entry.scan_count,
            risk_level=risk_level,
            settings=self._settings,
        )

        evaluation = EvaluationResult(
            mint=entry.mint,
            final_score=round(final_score, 2),
            confidence=round(conf, 2),
            risk_level=risk_level,
            market_mode=market_mode,
            reasons=reasons,
            engine_scores=engine_scores,
            scan_count=entry.scan_count,
            eligible_for_alert=eligible,
            evaluated_at=utcnow_iso(),
        )

        logger.info(
            "Scored %s: score=%.1f conf=%.1f%% risk=%s eligible=%s",
            entry.mint[:12],
            final_score,
            conf,
            risk_level.value,
            eligible,
        )
        return evaluation


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_weights(settings: "Settings") -> dict[str, float]:
    """Build engine weight map from settings, normalising to sum=1."""
    raw = {
        "trend":        settings.score_weight_trend,
        "volume":       settings.score_weight_volume,
        "buy_pressure": settings.score_weight_buy_pressure,
        "liquidity":    settings.score_weight_liquidity,
        "market_cap":   settings.score_weight_market_cap,
        "stability":    settings.score_weight_stability,
        "age":          settings.score_weight_age,
        "social":       settings.score_weight_social,
    }
    total = sum(raw.values())
    if total <= 0:
        return _DEFAULT_WEIGHTS.copy()
    return {k: v / total for k, v in raw.items()}


def _weighted_score(
    scores: dict[str, ScoreResult],
    weights: dict[str, float],
) -> float:
    """
    Compute a weighted normalised score in [0, 100].

    Each engine's normalised score (0–1) is multiplied by its weight,
    summed, then scaled to 100.  The risk engine score is excluded from
    weighting (it determines risk_level separately).
    """
    total = 0.0
    for key, weight in weights.items():
        result = scores.get(key)
        if result is None:
            continue
        total += result.normalized * weight
    return total * 100.0


def _apply_market_mode(score: float, mode: MarketMode) -> float:
    """Apply a small market-mode multiplier to the raw weighted score."""
    multipliers = {
        MarketMode.BULL: 1.05,
        MarketMode.NEUTRAL: 1.0,
        MarketMode.WEAK: 0.92,
    }
    return min(100.0, score * multipliers.get(mode, 1.0))


def _data_complete(token: "TokenData") -> bool:
    """Return True when all core numeric fields are present."""
    return all([
        token.price_usd is not None,
        token.market_cap_usd is not None,
        token.volume_24h_usd is not None,
        token.liquidity_usd is not None,
    ])


def _collect_reasons(
    scores: dict[str, ScoreResult],
    risk_level: RiskLevel,
    confidence: float,
) -> list[str]:
    """
    Collect one reason string per engine, ordered by score contribution.
    Only include engines that have meaningful data (reason not generic).
    """
    reasons: list[str] = []
    # Order by importance
    ordered = ["trend", "volume", "buy_pressure", "liquidity",
               "market_cap", "stability", "risk", "age", "social"]
    for key in ordered:
        result = scores.get(key)
        if result and result.reason:
            reasons.append(result.reason)
    reasons.append(f"Confidence: {confidence:.0f}%")
    return reasons


def _is_eligible(
    final_score: float,
    confidence: float,
    scan_count: int,
    risk_level: RiskLevel,
    settings: "Settings",
) -> bool:
    """
    Return True when all alert conditions are met.

    Conditions (all must pass):
    1. score >= min_alert_score
    2. confidence >= min_alert_confidence
    3. scan_count >= min_alert_scans
    4. risk_level is acceptable (not HIGH or CRITICAL by default)

    All numeric thresholds are read from runtime overrides first (set via
    /editfilters), falling back to the hardcoded settings values.
    """
    from app.config.settings import get_runtime_override

    min_score = float(
        get_runtime_override("min_alert_score") or settings.min_alert_score
    )
    min_confidence = float(
        get_runtime_override("min_alert_confidence") or settings.min_alert_confidence
    )
    min_scans = int(
        get_runtime_override("min_alert_scans") or settings.min_alert_scans
    )

    try:
        max_risk = RiskLevel.from_string(settings.max_alert_risk)
    except Exception:
        max_risk = RiskLevel.MEDIUM

    if final_score < min_score:
        return False
    if confidence < min_confidence:
        return False
    if scan_count < min_scans:
        return False
    if not risk_level.is_acceptable(max_risk):
        return False
    return True
