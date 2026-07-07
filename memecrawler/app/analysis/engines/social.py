"""
Social Engine.

Evaluates metadata quality: website, Twitter/X, Telegram, description,
logo presence, and whether the token has a meaningful symbol and name.

Data sourced from RugCheck report (if available) and TokenData fields.

max_score: 10
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.analysis.models import ScoreResult

logger = logging.getLogger(__name__)

MAX_SCORE: float = 10.0


def evaluate(
    symbol: str,
    name: str,
    rugcheck_data: Optional[dict[str, Any]] = None,
) -> ScoreResult:
    """
    Evaluate social presence and metadata quality.

    Parameters
    ----------
    symbol:
        Token ticker symbol (from TokenData).
    name:
        Token name (from TokenData).
    rugcheck_data:
        Full RugCheck report dict (optional).  Used to extract social links.

    Returns
    -------
    ScoreResult
    """
    score = 0.0
    signals: list[str] = []
    missing: list[str] = []

    # ── Basic metadata ────────────────────────────────────────────────────
    if symbol and len(symbol.strip()) >= 1:
        score += 1.5
        signals.append("symbol")
    else:
        missing.append("symbol")

    if name and len(name.strip()) >= 2:
        score += 1.5
        signals.append("name")
    else:
        missing.append("name")

    # ── RugCheck social links ─────────────────────────────────────────────
    if rugcheck_data:
        token_meta = rugcheck_data.get("tokenMeta") or {}
        markets = rugcheck_data.get("markets") or []

        # Website
        if _has_link(token_meta.get("website")):
            score += 2.0
            signals.append("website")
        else:
            missing.append("website")

        # Twitter/X
        if _has_link(token_meta.get("twitter")):
            score += 2.0
            signals.append("twitter")
        else:
            missing.append("twitter")

        # Telegram
        if _has_link(token_meta.get("telegram")):
            score += 1.5
            signals.append("telegram")
        else:
            missing.append("telegram")

        # Description / image
        if token_meta.get("description") or token_meta.get("image"):
            score += 1.5
            signals.append("description/logo")
        else:
            missing.append("description/logo")

    else:
        # No RugCheck data — partial credit only for what we can verify
        # from the token symbol/name (already scored above)
        pass

    total = min(MAX_SCORE, score)

    details: dict[str, Any] = {
        "signals_present": signals,
        "signals_missing": missing,
        "rugcheck_data_available": rugcheck_data is not None,
        "raw_score": round(score, 2),
    }

    reason = _build_reason(signals, missing, rugcheck_data is not None)
    return ScoreResult(score=round(total, 2), max_score=MAX_SCORE, reason=reason, details=details)


def _has_link(value: Any) -> bool:
    """Return True when a social link is non-empty."""
    return bool(value and isinstance(value, str) and value.strip())


def _build_reason(
    signals: list[str],
    missing: list[str],
    has_rugcheck: bool,
) -> str:
    if not has_rugcheck:
        present = ", ".join(signals) if signals else "none"
        return f"Limited metadata (no RugCheck data): {present} present"

    if not missing:
        return f"Complete social presence: {', '.join(signals)}"
    if signals:
        return (
            f"Partial social presence: {', '.join(signals)} present; "
            f"{', '.join(missing)} missing"
        )
    return "No social metadata detected"
