"""
Trend Engine.

Analyses historical scan records to measure directional momentum across
market cap, liquidity, volume, and transaction activity.

Rewards steady, consistent growth.
Penalises sudden pumps (spike without follow-through).

max_score: 50
"""

from __future__ import annotations

import logging
from statistics import mean, stdev
from typing import Any, Optional

from app.analysis.models import ScoreResult

logger = logging.getLogger(__name__)

MAX_SCORE: float = 50.0
_MIN_RECORDS: int = 2


def evaluate(
    history: list[dict[str, Any]],
    current_mc: Optional[float],
    current_liq: Optional[float],
    current_vol: Optional[float],
) -> ScoreResult:
    """
    Evaluate trend quality from historical scan records.

    Parameters
    ----------
    history:
        List of history rows (dict) ordered newest-first from the DB,
        each containing: market_cap_usd, liquidity_usd, volume_24h_usd,
        buys_5m, sells_5m, recorded_at.
    current_mc, current_liq, current_vol:
        Most recent market data for computing the latest delta.

    Returns
    -------
    ScoreResult
        score in [0, MAX_SCORE], reason, details.
    """
    # Chronological order (oldest first) is better for trend analysis
    rows = list(reversed(history)) if history else []

    if len(rows) < _MIN_RECORDS:
        return ScoreResult(
            score=0.0,
            max_score=MAX_SCORE,
            reason="Insufficient history for trend analysis",
            details={"records": len(rows)},
        )

    mc_changes = _pct_changes([r.get("market_cap_usd") for r in rows])
    liq_changes = _pct_changes([r.get("liquidity_usd") for r in rows])
    vol_changes = _pct_changes([r.get("volume_24h_usd") for r in rows])
    buy_changes = _pct_changes([r.get("buys_5m") for r in rows])
    sell_changes = _pct_changes([r.get("sells_5m") for r in rows])

    mc_score = _score_metric(mc_changes, weight=12.0)
    liq_score = _score_metric(liq_changes, weight=12.0)
    vol_score = _score_metric(vol_changes, weight=10.0)
    buy_score = _score_metric(buy_changes, weight=8.0)
    # Increasing sells is bad; invert
    sell_score = _score_metric([-x for x in sell_changes], weight=8.0)

    raw = mc_score + liq_score + vol_score + buy_score + sell_score

    # Consistency bonus/penalty: reward low variance in changes
    all_changes = mc_changes + liq_changes + vol_changes
    consistency_adj = _consistency_adjustment(all_changes)
    raw = max(0.0, min(MAX_SCORE, raw + consistency_adj))

    details: dict[str, Any] = {
        "records": len(rows),
        "mc_score": round(mc_score, 2),
        "liq_score": round(liq_score, 2),
        "vol_score": round(vol_score, 2),
        "buy_score": round(buy_score, 2),
        "sell_score": round(sell_score, 2),
        "consistency_adj": round(consistency_adj, 2),
        "pump_detected": _pump_detected(mc_changes),
    }

    reason = _build_reason(mc_changes, liq_changes, vol_changes, raw)

    return ScoreResult(score=round(raw, 2), max_score=MAX_SCORE, reason=reason, details=details)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pct_changes(values: list[Optional[float]]) -> list[float]:
    """Compute successive percentage changes, skipping None pairs."""
    changes: list[float] = []
    clean = [v for v in values if v is not None and v > 0]
    for i in range(1, len(clean)):
        prev, curr = clean[i - 1], clean[i]
        if prev > 0:
            changes.append((curr - prev) / prev * 100)
    return changes


def _score_metric(changes: list[float], weight: float) -> float:
    """
    Convert a list of percentage changes into a score in [0, weight].

    Positive average growth → close to weight.
    Flat → weight / 2.
    Negative → close to 0.
    Pump detected → penalised.
    """
    if not changes:
        return weight * 0.3  # partial credit for no data

    avg = mean(changes)

    if _pump_detected(changes):
        # Heavy penalty for pump-and-dump pattern
        return weight * 0.1

    # Map avg change to a 0-1 score:
    # -50% → 0, 0% → 0.4, +10% → 0.7, +30% → 0.9, +100% → 1.0
    # Use a soft-sigmoid-like mapping
    if avg <= -50:
        ratio = 0.0
    elif avg <= 0:
        ratio = 0.4 + avg / 50 * 0.4   # 0 to 0.4
    elif avg <= 30:
        ratio = 0.4 + avg / 30 * 0.5   # 0.4 to 0.9
    else:
        ratio = min(1.0, 0.9 + (avg - 30) / 200 * 0.1)

    return weight * ratio


def _pump_detected(changes: list[float]) -> bool:
    """
    Return True when a pump pattern is detected.

    A pump = a single change > 100% followed by a negative change.
    """
    for i, ch in enumerate(changes):
        if ch > 100 and i + 1 < len(changes) and changes[i + 1] < 0:
            return True
    return False


def _consistency_adjustment(changes: list[float]) -> float:
    """
    Return a score adjustment [-5, +5] based on growth consistency.

    Low variance → bonus.
    High variance → penalty.
    """
    if len(changes) < 2:
        return 0.0
    try:
        sd = stdev(changes)
    except Exception:
        return 0.0

    if sd < 10:
        return 5.0    # Very consistent
    if sd < 30:
        return 2.0
    if sd < 60:
        return 0.0
    if sd < 100:
        return -2.0
    return -5.0       # Very volatile


def _build_reason(
    mc: list[float],
    liq: list[float],
    vol: list[float],
    raw: float,
) -> str:
    parts = []
    if mc:
        avg_mc = mean(mc)
        parts.append(f"MC {avg_mc:+.0f}% avg")
    if liq:
        avg_liq = mean(liq)
        parts.append(f"liq {avg_liq:+.0f}% avg")
    if vol:
        avg_vol = mean(vol)
        parts.append(f"vol {avg_vol:+.0f}% avg")
    if not parts:
        return "No trend data"
    label = "Strong uptrend" if raw >= 35 else ("Neutral trend" if raw >= 20 else "Weak/negative trend")
    return f"{label}: {'; '.join(parts)}"
