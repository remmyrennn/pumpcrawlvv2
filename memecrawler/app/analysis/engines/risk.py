"""
Risk Engine.

Evaluates token risk using RugCheck data and on-chain indicators.
Produces a RiskLevel (LOW / MEDIUM / HIGH / CRITICAL) and a score.

A higher score means LOWER risk (safer token).

max_score: 20
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.analysis.models import RiskLevel, ScoreResult

logger = logging.getLogger(__name__)

MAX_SCORE: float = 20.0

# RugCheck score scale: 0 (risky) – 1000 (very safe)
_RUGCHECK_SAFE_THRESHOLD: int = 700
_RUGCHECK_OK_THRESHOLD: int = 400

# Known high-risk flag identifiers from RugCheck
_CRITICAL_FLAGS: frozenset[str] = frozenset({
    "freeze_authority_enabled",
    "mint_authority_enabled",
    "copycat_token",
    "honeypot",
})
_HIGH_FLAGS: frozenset[str] = frozenset({
    "high_holder_concentration",
    "dev_wallet_large",
    "blacklist_function",
    "low_holder_count",
})
_MEDIUM_FLAGS: frozenset[str] = frozenset({
    "low_liquidity",
    "single_pool",
    "no_website",
    "no_social",
})


def evaluate(
    rugcheck_data: Optional[dict[str, Any]] = None,
) -> tuple[RiskLevel, ScoreResult]:
    """
    Evaluate risk from RugCheck report data.

    Parameters
    ----------
    rugcheck_data:
        Full RugCheck report dict (from ``get_token_report``).
        When None, a conservative UNKNOWN risk is returned.

    Returns
    -------
    tuple[RiskLevel, ScoreResult]
        The risk level and a ScoreResult (higher score = safer).
    """
    if rugcheck_data is None:
        # No RugCheck data — conservative partial score
        result = ScoreResult(
            score=MAX_SCORE * 0.4,
            max_score=MAX_SCORE,
            reason="No RugCheck data — risk unknown",
            details={"data_available": False},
        )
        return RiskLevel.UNKNOWN, result

    score = MAX_SCORE
    flags_triggered: list[str] = []
    details: dict[str, Any] = {"data_available": True}

    # ── RugCheck score ────────────────────────────────────────────────────
    rc_score = _extract_rugcheck_score(rugcheck_data)
    details["rugcheck_score"] = rc_score

    if rc_score is not None:
        if rc_score >= _RUGCHECK_SAFE_THRESHOLD:
            pass   # Full score
        elif rc_score >= _RUGCHECK_OK_THRESHOLD:
            score -= 3.0
        else:
            score -= 8.0

    # ── Risk flags ────────────────────────────────────────────────────────
    raw_flags = _extract_flags(rugcheck_data)
    details["raw_flag_count"] = len(raw_flags)

    critical_hits = raw_flags & _CRITICAL_FLAGS
    high_hits = raw_flags & _HIGH_FLAGS
    medium_hits = raw_flags & _MEDIUM_FLAGS

    score -= len(critical_hits) * 6.0
    score -= len(high_hits) * 3.0
    score -= len(medium_hits) * 1.0

    flags_triggered = (
        list(critical_hits) + list(high_hits) + list(medium_hits)
    )
    details["flags_triggered"] = flags_triggered

    # ── Holder concentration ──────────────────────────────────────────────
    top_holder_pct = _extract_top_holder_pct(rugcheck_data)
    details["top_holder_pct"] = top_holder_pct
    if top_holder_pct is not None:
        if top_holder_pct > 50:
            score -= 5.0
            flags_triggered.append("top_holder_>50pct")
        elif top_holder_pct > 30:
            score -= 3.0
        elif top_holder_pct > 20:
            score -= 1.0

    # ── Mint / Freeze authority checks ────────────────────────────────────
    if rugcheck_data.get("mintAuthority"):
        score -= 5.0
        flags_triggered.append("mint_authority_active")
    if rugcheck_data.get("freezeAuthority"):
        score -= 4.0
        flags_triggered.append("freeze_authority_active")

    score = max(0.0, min(MAX_SCORE, score))
    details["flags_triggered"] = list(set(flags_triggered))

    risk_level = _score_to_risk(score, critical_hits, high_hits)
    reason = _build_reason(risk_level, flags_triggered, rc_score)

    return risk_level, ScoreResult(
        score=round(score, 2),
        max_score=MAX_SCORE,
        reason=reason,
        details=details,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_rugcheck_score(data: dict[str, Any]) -> Optional[int]:
    """Extract the numeric RugCheck safety score (0–1000)."""
    raw = data.get("score") or data.get("risk_score") or data.get("riskScore")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _extract_flags(data: dict[str, Any]) -> frozenset[str]:
    """Extract flag identifiers from RugCheck response."""
    flags: set[str] = set()

    # RugCheck full report: {"risks": [{"name": "...", "level": "..."}]}
    risks = data.get("risks") or []
    for risk in risks:
        name = risk.get("name") or risk.get("id") or ""
        if name:
            # Normalise to snake_case
            flags.add(name.lower().replace(" ", "_").replace("-", "_"))

    # Also check top-level boolean flags
    if data.get("mintAuthority"):
        flags.add("mint_authority_enabled")
    if data.get("freezeAuthority"):
        flags.add("freeze_authority_enabled")

    return frozenset(flags)


def _extract_top_holder_pct(data: dict[str, Any]) -> Optional[float]:
    """Return the top-holder percentage if available."""
    # RugCheck v1 report may contain topHolders list
    top_holders = data.get("topHolders") or []
    if not top_holders:
        return None
    try:
        # Sum of pct for the top holder
        first = top_holders[0]
        pct = first.get("pct") or first.get("percentage") or 0
        return float(pct) * (100 if float(pct) <= 1.0 else 1)   # normalise
    except (IndexError, TypeError, ValueError):
        return None


def _score_to_risk(
    score: float,
    critical_hits: frozenset[str],
    high_hits: frozenset[str],
) -> RiskLevel:
    """Convert safety score + flag hits to a RiskLevel."""
    if critical_hits or score < 5:
        return RiskLevel.CRITICAL
    if high_hits or score < 10:
        return RiskLevel.HIGH
    if score < 15:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def _build_reason(
    risk: RiskLevel,
    flags: list[str],
    rc_score: Optional[int],
) -> str:
    score_str = f" (RC score: {rc_score})" if rc_score is not None else ""
    if not flags:
        return f"Risk: {risk.value}{score_str} — no dangerous flags"
    top_flags = ", ".join(flags[:3])
    more = f" +{len(flags) - 3} more" if len(flags) > 3 else ""
    return f"Risk: {risk.value}{score_str} — flags: {top_flags}{more}"
