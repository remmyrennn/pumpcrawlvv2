"""
Intelligence Layer — Sprint 3.

Provides modular, weighted, stateless scoring engines, market mode detection,
leaderboard ranking, alert dispatch, and milestone tracking for MemeCrawler.

Sub-modules
-----------
models          — Shared domain types (RiskLevel, MarketMode, ScoreResult, etc.)
engines/        — Individual scoring engines (trend, liquidity, volume, …)
scorer          — ScoringEngine: orchestrates engines → EvaluationResult
market_mode     — MarketModeDetector: BULL / NEUTRAL / WEAK
ranking         — RankingEngine: leaderboard computation and persistence
alert_engine    — AlertEngine: de-duplicated conviction-based alerts
milestone       — MilestoneTracker: post-alert performance monitoring
"""
