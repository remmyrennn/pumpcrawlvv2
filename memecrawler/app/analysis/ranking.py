"""
Ranking Engine.

Maintains a live leaderboard of the most promising tokens.

Rankings are computed from the evaluations table and persisted to the
rankings table for fast retrieval.

Leaderboard types:
- conviction:    Highest final_score
- confidence:    Highest confidence percentage
- improvement:   Biggest score improvement vs previous evaluation

Sprint 3.
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from app.analysis.models import EvaluationResult
from app.utils.time_utils import utcnow_iso

if TYPE_CHECKING:
    from app.database.manager import DatabaseManager

logger = logging.getLogger(__name__)

# Number of entries to keep per leaderboard type
_TOP_N: int = 10


class RankingEngine:
    """
    Computes and persists token rankings.

    Parameters
    ----------
    db:
        Open DatabaseManager instance.
    """

    def __init__(self, db: "DatabaseManager") -> None:
        self._db = db

    # ── Update ────────────────────────────────────────────────────────────

    async def update(self, evaluation: EvaluationResult) -> None:
        """
        Refresh rankings after a new evaluation is stored.

        Called by TokenScanner after each scored token.

        Parameters
        ----------
        evaluation:
            The freshly computed evaluation result.
        """
        try:
            await self._upsert_rank(evaluation)
            logger.debug(
                "Ranking updated for %s (score=%.1f conf=%.1f%%)",
                evaluation.mint[:12],
                evaluation.final_score,
                evaluation.confidence,
            )
        except Exception as exc:
            logger.warning("RankingEngine.update failed for %s: %s", evaluation.mint[:12], exc)

    async def _upsert_rank(self, ev: EvaluationResult) -> None:
        """Insert or replace the ranking entry for this mint."""
        now = utcnow_iso()
        await self._db.execute(
            """
            INSERT INTO rankings (mint, symbol, score, confidence, risk_level,
                                  rank, rank_type, ranked_at)
            VALUES (?, ?, ?, ?, ?, 0, 'conviction', ?)
            ON CONFLICT(mint) DO UPDATE SET
                score      = excluded.score,
                confidence = excluded.confidence,
                risk_level = excluded.risk_level,
                ranked_at  = excluded.ranked_at
            """,
            (
                ev.mint,
                "",   # symbol updated below
                ev.final_score,
                ev.confidence,
                ev.risk_level.value,
                now,
            ),
        )
        # Refresh ordinal ranks for the conviction leaderboard
        await self._refresh_ordinal_ranks()

    async def _refresh_ordinal_ranks(self) -> None:
        """Recompute integer rank positions for all tracked tokens."""
        rows = await self._db.fetchall(
            "SELECT mint FROM rankings ORDER BY score DESC, confidence DESC"
        )
        async with self._db.cursor() as cur:
            for i, row in enumerate(rows, start=1):
                await cur.execute(
                    "UPDATE rankings SET rank = ? WHERE mint = ?",
                    (i, row["mint"]),
                )

    # ── Read ──────────────────────────────────────────────────────────────

    async def get_top(
        self,
        n: int = 10,
        rank_type: str = "conviction",
    ) -> list[dict[str, Any]]:
        """
        Return the top-N ranked tokens.

        Parameters
        ----------
        n:
            Number of entries to return (max _TOP_N).
        rank_type:
            "conviction" (score), "confidence", or "improvement".
        """
        n = min(n, _TOP_N)

        if rank_type == "confidence":
            order_col = "confidence DESC, score DESC"
        elif rank_type == "improvement":
            # Improvement is not stored in rankings — fall back to score
            order_col = "score DESC, confidence DESC"
        else:
            order_col = "score DESC, confidence DESC"

        rows = await self._db.fetchall(
            f"""
            SELECT r.mint, r.score, r.confidence, r.risk_level, r.rank,
                   r.ranked_at,
                   w.symbol, w.name, w.state, w.market_cap_usd, w.liquidity_usd
            FROM rankings r
            LEFT JOIN watchlist w ON w.mint = r.mint
            ORDER BY {order_col}
            LIMIT ?
            """,
            (n,),
        )
        return [dict(row) for row in rows]

    async def get_improvement_top(self, n: int = 10) -> list[dict[str, Any]]:
        """
        Return top-N tokens by score improvement vs their previous evaluation.
        """
        n = min(n, _TOP_N)
        # Fetch latest and second-latest score per mint from evaluations
        rows = await self._db.fetchall(
            """
            SELECT e1.mint,
                   e1.score       AS latest_score,
                   e2.score       AS prev_score,
                   (e1.score - COALESCE(e2.score, e1.score)) AS improvement,
                   e1.confidence,
                   e1.risk_level,
                   w.symbol, w.market_cap_usd
            FROM evaluations e1
            LEFT JOIN evaluations e2
                   ON e2.mint = e1.mint
                  AND e2.evaluated_at = (
                        SELECT MAX(e3.evaluated_at)
                        FROM evaluations e3
                        WHERE e3.mint = e1.mint
                          AND e3.evaluated_at < e1.evaluated_at
                      )
            LEFT JOIN watchlist w ON w.mint = e1.mint
            WHERE e1.evaluated_at = (
                SELECT MAX(e4.evaluated_at)
                FROM evaluations e4
                WHERE e4.mint = e1.mint
            )
            ORDER BY improvement DESC
            LIMIT ?
            """,
            (n,),
        )
        return [dict(row) for row in rows]

    def info(self) -> dict[str, Any]:
        """Return metadata for diagnostics."""
        return {"top_n": _TOP_N}
