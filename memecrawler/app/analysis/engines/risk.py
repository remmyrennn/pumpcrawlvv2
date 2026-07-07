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
        result = ScoreResult(
            score=MAX_SCORE * 0.4,
            max_score=MAX_SCORE,
            reason="No RugCheck data — risk unknown",
            details={"data_available": False},
        )
        return RiskLevel.UNKNOWN, result

    score = MAX_SCORE
    details: dict[str, Any] = {"data_available": True}

    # ── RugCheck score ────────────────────────────────────────────────────
    rc_score = _extract_rugcheck_score(rugcheck_data)
    details["rugcheck_score"] = rc_score

    if rc_score is not None:
        if rc_score >= _RUGCHECK_SAFE_THRESHOLD:
            pass
        elif rc_score >= _RUGCHECK_OK_THRESHOLD:
            score -= 3.0
        else:
            score -= 8.0

    # ── Risk flags ────────────────────────────────────────────────────────
    # _extract_flags already includes mint/freeze authority from top-level fields.
    # We do NOT add separate inline deductions for mint/freeze authority here —
    # that was a double-counting bug (they were penalised via _CRITICAL_FLAGS AND
    # via the inline check below, resulting in -11 instead of -6 for mint, etc.).
    raw_flags = _extract_flags(rugcheck_data)
    details["raw_flag_count"] = len(raw_flags)

    critical_hits = raw_flags & _CRITICAL_FLAGS
    high_hits = raw_flags & _HIGH_FLAGS
    medium_hits = raw_flags & _MEDIUM_FLAGS

    score -= len(critical_hits) * 6.0
    score -= len(high_hits) * 3.0
    score -= len(medium_hits) * 1.0

    flags_triggered: list[str] = (
        list(critical_hits) + list(high_hits) + list(medium_hits)
    )

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
            flags.add(name.lower().replace(" ", "_").replace("-", "_"))

    # Top-level boolean authority flags (only added here — not in evaluate())
    if data.get("mintAuthority"):
        flags.add("mint_authority_enabled")
    if data.get("freezeAuthority"):
        flags.add("freeze_authority_enabled")

    return frozenset(flags)


def _extract_top_holder_pct(data: dict[str, Any]) -> Optional[float]:
    """Return the top-holder percentage if available."""
    top_holders = data.get("topHolders") or []
    if not top_holders:
        return None
    try:
        first = top_holders[0]
        pct = first.get("pct") or first.get("percentage") or 0
        val = float(pct)
        # Normalise: RugCheck may return 0.45 (fraction) or 45.0 (percent)
        return val * 100 if val <= 1.0 else val
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
