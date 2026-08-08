"""Application settings, loaded from environment variables (and optionally .env).

One cached `get_settings()` — every other module imports from here. No `os.getenv` anywhere else.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration. Read once at process start."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- App ----------------------------------------------------------------
    app_name: str = "cinemaseat-api"
    app_version: str = "0.1.0"
    environment: Literal["local", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    public_base_url: str = "https://poridhi-hackathon.shadathossainrony.dev"
    cors_origins: str = "http://localhost:5173"

    # --- Booking rules (REQ-19 lives here) ----------------------------------
    hold_ttl_seconds: int = Field(default=120, ge=5, le=3600)
    payment_window_seconds: int = Field(default=90, ge=5, le=600)
    sweep_interval_seconds: int = Field(default=5, ge=1, le=300)
    reconcile_interval_seconds: int = Field(default=30, ge=1, le=600)
    max_seats_per_hold: int = Field(default=6, ge=1, le=20)

    # --- OTP ----------------------------------------------------------------
    otp_required: bool = True
    otp_max_attempts: int = Field(default=5, ge=1, le=20)
    otp_resend_cooldown_seconds: int = Field(default=30, ge=1, le=600)

    # --- Database -----------------------------------------------------------
    postgres_user: str = "cinemaseat"
    postgres_password: str = "cinemaseat_dev_pw"
    postgres_db: str = "cinemaseat"
    postgres_host: str = "db"
    postgres_port: int = 5432
    db_pool_size: int = Field(default=10, ge=1, le=50)
    db_max_overflow: int = Field(default=10, ge=0, le=50)
    db_pool_timeout: int = Field(default=10, ge=1, le=60)

    # --- Gateway ------------------------------------------------------------
    gateway_base_url: str = "http://gateway:9000"
    gateway_callback_url: str = "http://api:8000/payments/callback"
    gateway_timeout_seconds: float = Field(default=5.0, ge=0.5, le=30.0)
    gateway_health_timeout_seconds: float = Field(default=1.0, ge=0.1, le=10.0)
    gateway_breaker_threshold: int = Field(default=5, ge=1, le=100)
    gateway_breaker_reset_seconds: int = Field(default=30, ge=1, le=600)
    # Per payment_gateway.md: GATEWAY_SECRET default is "z2p-2026-secret".
    # The gateway signs every callback with this — anyone on the internet can
    # POST to our webhook URL, so we verify before we apply.
    gateway_secret: str = "z2p-2026-secret"
    # Set to False to skip HMAC verification (e.g. local dev with a custom
    # mock that doesn't sign). The production docker-compose leaves it on.
    gateway_signature_required: bool = True

    # --- Rate limiting ------------------------------------------------------
    rate_limit_hold_per_minute: int = Field(default=120, ge=10)
    rate_limit_default_per_minute: int = Field(default=600, ge=10)

    # --- Sessions (INF-08; only used if session token ships) ----------------
    session_secret: str = "dev-only-not-a-secret"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        """Built from the parts above — one source of truth."""
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings instance — read once per process."""
    return Settings()
