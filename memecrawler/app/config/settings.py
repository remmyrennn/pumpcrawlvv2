"""
Centralised application configuration.

All values are loaded from environment variables (via .env).
Never hardcode secrets or configuration values.
"""

from __future__ import annotations

import re
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Pydantic-based settings loaded from environment variables / .env file.

    Sections
    --------
    - Bot           Telegram credentials and targeting
    - APIs          Third-party API keys
    - Supabase      Cloud database (prepared for Sprint 4)
    - Database      Local SQLite path
    - Logging       Log level
    - Timing        Scan and heartbeat intervals (seconds)
    - Discovery     Filters applied during token discovery
    - Features      Feature flags controlling which integrations are active
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Bot ──────────────────────────────────────────────────────────────
    bot_token: str = Field(
        default="",
        description="Telegram Bot API token from @BotFather.",
    )
    authorized_users: str = Field(
        default="",
        description="Comma-separated Telegram user IDs allowed to interact with the bot.",
    )
    target_chat: str = Field(
        default="",
        description="Telegram chat ID where alerts are sent.",
    )

    # ── APIs ─────────────────────────────────────────────────────────────
    helius_api_key: str = Field(
        default="",
        description="Helius API key for enhanced Solana RPC.",
    )

    # ── Supabase (Sprint 4) ───────────────────────────────────────────────
    supabase_url: str = Field(
        default="",
        description="Supabase project URL (Sprint 4).",
    )
    supabase_key: str = Field(
        default="",
        description="Supabase anon/service key (Sprint 4).",
    )

    # ── Database ─────────────────────────────────────────────────────────
    sqlite_path: str = Field(
        default="memecrawler.db",
        description="Path to the SQLite database file.",
    )

    # ── Logging ───────────────────────────────────────────────────────────
    log_level: str = Field(
        default="INFO",
        description="Logging level: DEBUG, INFO, WARNING, ERROR, CRITICAL.",
    )

    # ── Timing ───────────────────────────────────────────────────────────
    scan_interval: int = Field(
        default=300,
        ge=20,
        description="How often (seconds) the scanner runs a full discovery + scan cycle.",
    )
    heartbeat_interval: int = Field(
        default=3600,
        ge=60,
        description="How often (seconds) the heartbeat message is sent.",
    )

    # ── Discovery filters ─────────────────────────────────────────────────
    min_liquidity_usd: float = Field(
        default=500.0,
        ge=0.0,
        description="Minimum USD liquidity for a token to pass the discovery filter.",
    )
    discovery_limit: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Maximum number of tokens to fetch per discovery cycle per provider.",
    )
    blacklisted_tokens: str = Field(
        default="",
        description="Comma-separated list of token mint addresses to always reject.",
    )
    blacklisted_developers: str = Field(
        default="",
        description="Comma-separated list of developer wallet addresses to always reject.",
    )

    # ── Priority thresholds ───────────────────────────────────────────────
    priority_critical_volume: float = Field(
        default=500_000.0,
        description="24h volume (USD) threshold for CRITICAL priority.",
    )
    priority_critical_liquidity: float = Field(
        default=100_000.0,
        description="Liquidity (USD) threshold for CRITICAL priority.",
    )
    priority_high_volume: float = Field(
        default=100_000.0,
        description="24h volume (USD) threshold for HIGH priority.",
    )
    priority_high_liquidity: float = Field(
        default=50_000.0,
        description="Liquidity (USD) threshold for HIGH priority.",
    )
    priority_medium_volume: float = Field(
        default=10_000.0,
        description="24h volume (USD) threshold for MEDIUM priority.",
    )
    priority_medium_liquidity: float = Field(
        default=10_000.0,
        description="Liquidity (USD) threshold for MEDIUM priority.",
    )

    # ── Feature flags ─────────────────────────────────────────────────────
    enable_pumpfun: bool = Field(
        default=True,
        description="Enable Pump.fun provider.",
    )
    enable_dexscreener: bool = Field(
        default=True,
        description="Enable DexScreener provider.",
    )
    enable_helius: bool = Field(
        default=False,
        description="Enable Helius provider (requires HELIUS_API_KEY).",
    )
    enable_rugcheck: bool = Field(
        default=True,
        description="Enable RugCheck provider.",
    )
    enable_heartbeat: bool = Field(
        default=True,
        description="Enable periodic Telegram heartbeat messages.",
    )
    enable_scanner: bool = Field(
        default=True,
        description="Enable the token scanner job.",
    )
    enable_supabase_sync: bool = Field(
        default=False,
        description="Enable Supabase synchronisation (Sprint 4).",
    )

    # ── Derived helpers ──────────────────────────────────────────────────
    @field_validator("log_level", mode="before")
    @classmethod
    def normalise_log_level(cls, value: str) -> str:
        """Ensure log level is always uppercased."""
        return value.upper()

    @property
    def authorized_user_ids(self) -> list[int]:
        """Return authorised Telegram user IDs as a list of integers.

        Accepts comma- or whitespace-separated lists and silently ignores
        any non-numeric tokens so stray text in the env var never crashes
        startup (e.g. "123456 789012 # main account").

        Only tokens that match the exact pattern ``-?\\d+`` are accepted,
        which means ``--1`` or ``abc`` are silently discarded while valid
        negative IDs such as ``-100123456`` (channel IDs) are kept.
        """
        tokens = re.split(r"[\s,]+", self.authorized_users.strip())
        return [int(t) for t in tokens if re.fullmatch(r"-?\d+", t)]

    @property
    def blacklisted_token_set(self) -> frozenset[str]:
        """Return blacklisted token mints as a frozenset for O(1) lookup."""
        tokens = re.split(r"[\s,]+", self.blacklisted_tokens.strip())
        return frozenset(t for t in tokens if t)

    @property
    def blacklisted_developer_set(self) -> frozenset[str]:
        """Return blacklisted developer addresses as a frozenset."""
        tokens = re.split(r"[\s,]+", self.blacklisted_developers.strip())
        return frozenset(t for t in tokens if t)

    @property
    def bot_configured(self) -> bool:
        """True when a bot token has been supplied."""
        return bool(self.bot_token.strip())

    @property
    def helius_configured(self) -> bool:
        """True when a Helius API key has been supplied."""
        return bool(self.helius_api_key.strip())

    # ── Sprint 3: Alert thresholds ────────────────────────────────────────
    min_alert_score: float = Field(
        default=65.0,
        ge=0.0,
        le=100.0,
        description="Minimum conviction score (0–100) required to dispatch an alert.",
    )
    min_alert_confidence: float = Field(
        default=60.0,
        ge=0.0,
        le=100.0,
        description="Minimum confidence percentage (0–100) required to dispatch an alert.",
    )
    min_alert_scans: int = Field(
        default=3,
        ge=1,
        description="Minimum number of rescans before an alert can be dispatched.",
    )
    max_alert_risk: str = Field(
        default="MEDIUM",
        description="Maximum acceptable risk level for alerts: LOW, MEDIUM, HIGH, CRITICAL.",
    )

    # ── Sprint 3: Market mode thresholds ──────────────────────────────────
    market_mode_bull_ratio: float = Field(
        default=0.60,
        ge=0.0,
        le=1.0,
        description="Fraction of tokens with positive trend required for BULL market mode.",
    )
    market_mode_weak_ratio: float = Field(
        default=0.30,
        ge=0.0,
        le=1.0,
        description="Fraction of tokens with positive trend below which WEAK mode is declared.",
    )

    # ── Sprint 3: Scoring engine weights ──────────────────────────────────
    # These do NOT need to sum to 1.0 — the scorer normalises them.
    score_weight_trend: float = Field(
        default=0.25,
        ge=0.0,
        description="Weight for the Trend scoring engine.",
    )
    score_weight_volume: float = Field(
        default=0.15,
        ge=0.0,
        description="Weight for the Volume scoring engine.",
    )
    score_weight_buy_pressure: float = Field(
        default=0.15,
        ge=0.0,
        description="Weight for the Buy Pressure scoring engine.",
    )
    score_weight_liquidity: float = Field(
        default=0.15,
        ge=0.0,
        description="Weight for the Liquidity scoring engine.",
    )
    score_weight_market_cap: float = Field(
        default=0.10,
        ge=0.0,
        description="Weight for the Market Cap scoring engine.",
    )
    score_weight_stability: float = Field(
        default=0.10,
        ge=0.0,
        description="Weight for the Stability scoring engine.",
    )
    score_weight_age: float = Field(
        default=0.05,
        ge=0.0,
        description="Weight for the Age scoring engine.",
    )
    score_weight_social: float = Field(
        default=0.05,
        ge=0.0,
        description="Weight for the Social scoring engine.",
    )

    @property
    def supabase_configured(self) -> bool:
        """True when Supabase credentials have been supplied."""
        return bool(self.supabase_url.strip() and self.supabase_key.strip())


# ── Runtime overrides (mutable, in-process only) ──────────────────────────────
# Modified by /editfilters Telegram command. Not persisted across restarts.

_runtime_overrides: dict[str, object] = {}


def get_runtime_override(key: str, default: object = None) -> object:
    """Return a runtime override if set, otherwise the supplied default."""
    return _runtime_overrides.get(key, default)


def set_runtime_override(key: str, value: object) -> None:
    """Store a runtime override value (overrides the corresponding Setting)."""
    _runtime_overrides[key] = value


def clear_runtime_override(key: str) -> None:
    """Remove a runtime override, restoring the Setting's default."""
    _runtime_overrides.pop(key, None)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return the application settings singleton.

    Uses ``@lru_cache`` so the .env file is read exactly once per process.
    """
    return Settings()
