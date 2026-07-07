"""
Centralised application configuration.

Credentials are hardcoded for personal use.
To add your Helius API key, find the line marked HELIUS_API_KEY below.
"""

from __future__ import annotations

import re
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings.

    Hardcoded defaults are used for all personal credentials.
    The model still supports .env overrides for any field if needed.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Bot ──────────────────────────────────────────────────────────────
    bot_token: str = Field(
        default="8885020162:AAFOlfxE0-ZnZ0W84bPzX8jTDEVGBSUWMhk",
        description="Telegram Bot API token from @BotFather.",
    )
    authorized_users: str = Field(
        default="7585996720",
        description="Comma-separated Telegram user IDs allowed to interact with the bot.",
    )
    target_chat: str = Field(
        default="1003947175145",
        description="Telegram chat ID where alerts are sent.",
    )

    # ── APIs ─────────────────────────────────────────────────────────────
    # ↓↓↓ PASTE YOUR HELIUS API KEY HERE ↓↓↓
    helius_api_key: str = Field(
        default="",
        description="Helius API key — get one free at https://dev.helius.xyz",
    )
    # ↑↑↑ HELIUS_API_KEY ↑↑↑

    # ── Supabase ─────────────────────────────────────────────────────────
    supabase_url: str = Field(
        default="https://dnmwljrhvxiwsdvrabyw.supabase.co",
        description="Supabase project URL.",
    )
    supabase_key: str = Field(
        default="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImRubXdsanJodnhpd3NkdnJhYnl3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODMyODIwOTEsImV4cCI6MjA5ODg1ODA5MX0.BGXzQV12nl13RdkXhPGFhSZKPQT025TfjCIa8wo97Ew",
        description="Supabase anon/service key.",
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
    broadcast_chats: str = Field(
        default="",
        description=(
            "Extra chat IDs to broadcast alerts and heartbeats to, with optional names. "
            "Format: 'chat_id:Name,chat_id:Name' e.g. '-100123:Alpha,-100456:Beta Calls'"
        ),
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
        default=False,
        description="Enable Pump.fun provider (disabled — blocked by Cloudflare on cloud IPs).",
    )
    enable_dexscreener: bool = Field(
        default=True,
        description="Enable DexScreener provider.",
    )
    enable_helius: bool = Field(
        default=False,
        description="Enable Helius provider (auto-enabled when helius_api_key is non-empty).",
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
        default=True,
        description="Enable Supabase synchronisation.",
    )

    # ── Alert thresholds ─────────────────────────────────────────────────
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

    # ── Market mode thresholds ────────────────────────────────────────────
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

    # ── Scoring engine weights ────────────────────────────────────────────
    score_weight_trend: float = Field(default=0.25, ge=0.0)
    score_weight_volume: float = Field(default=0.15, ge=0.0)
    score_weight_buy_pressure: float = Field(default=0.15, ge=0.0)
    score_weight_liquidity: float = Field(default=0.15, ge=0.0)
    score_weight_market_cap: float = Field(default=0.10, ge=0.0)
    score_weight_stability: float = Field(default=0.10, ge=0.0)
    score_weight_age: float = Field(default=0.05, ge=0.0)
    score_weight_social: float = Field(default=0.05, ge=0.0)

    # ── Maintenance ───────────────────────────────────────────────────────
    enable_maintenance: bool = Field(
        default=True,
        description="Enable the periodic DB maintenance job.",
    )
    maintenance_interval: int = Field(
        default=3600,
        ge=60,
        description="How often (seconds) routine maintenance runs.",
    )
    vacuum_interval: int = Field(
        default=86400,
        ge=3600,
        description="How often (seconds) SQLite VACUUM and integrity check runs.",
    )
    db_retention_days: int = Field(
        default=7,
        ge=1,
        description="Number of days to retain high-volume records.",
    )
    enable_db_backup: bool = Field(
        default=False,
        description="Enable automatic SQLite file backups on each VACUUM cycle.",
    )
    backup_dir: str = Field(
        default="backups",
        description="Directory where automatic DB backups are stored.",
    )

    # ── Derived helpers ──────────────────────────────────────────────────
    @field_validator("log_level", mode="before")
    @classmethod
    def normalise_log_level(cls, value: str) -> str:
        return value.upper()

    @property
    def effective_enable_helius(self) -> bool:
        """True when Helius is explicitly enabled OR an API key is present."""
        return self.enable_helius or bool(self.helius_api_key.strip())

    @property
    def broadcast_chat_list(self) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        if not self.broadcast_chats.strip():
            return result
        for entry in self.broadcast_chats.split(","):
            entry = entry.strip()
            if not entry:
                continue
            if ":" in entry:
                chat_id, _, name = entry.partition(":")
                result.append({"id": chat_id.strip(), "name": name.strip() or chat_id.strip()})
            else:
                result.append({"id": entry, "name": entry})
        return result

    @property
    def authorized_user_ids(self) -> list[int]:
        tokens = re.split(r"[\s,]+", self.authorized_users.strip())
        return [int(t) for t in tokens if re.fullmatch(r"-?\d+", t)]

    @property
    def blacklisted_token_set(self) -> frozenset[str]:
        tokens = re.split(r"[\s,]+", self.blacklisted_tokens.strip())
        return frozenset(t for t in tokens if t)

    @property
    def blacklisted_developer_set(self) -> frozenset[str]:
        tokens = re.split(r"[\s,]+", self.blacklisted_developers.strip())
        return frozenset(t for t in tokens if t)

    @property
    def bot_configured(self) -> bool:
        return bool(self.bot_token.strip())

    @property
    def helius_configured(self) -> bool:
        return bool(self.helius_api_key.strip())

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url.strip() and self.supabase_key.strip())


# ── Runtime overrides (mutable, in-process only) ──────────────────────────────
# Modified by /editfilters Telegram command. Not persisted across restarts.

_runtime_overrides: dict[str, object] = {}


def get_runtime_override(key: str, default: object = None) -> object:
    return _runtime_overrides.get(key, default)


def set_runtime_override(key: str, value: object) -> None:
    _runtime_overrides[key] = value


def clear_runtime_override(key: str) -> None:
    _runtime_overrides.pop(key, None)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the application settings singleton (loaded once per process)."""
    return Settings()
