"""
Configuration management - all settings from environment variables.
"""
import os
from dataclasses import dataclass, field


@dataclass
class SignalConfig:
    """Signal generation configuration."""
    min_confidence: int = field(
        default_factory=lambda: int(os.environ.get("MIN_CONFIDENCE", "60"))
    )
    max_account_exposure_pct: float = field(
        default_factory=lambda: float(os.environ.get("MAX_ACCOUNT_EXPOSURE_PCT", "0.10"))
    )
    max_positions_per_pair: int = field(
        default_factory=lambda: int(os.environ.get("MAX_POSITIONS_PER_PAIR", "5"))
    )
    candle_tracking_enabled: bool = field(
        default_factory=lambda: os.environ.get("CANDLE_TRACKING_ENABLED", "true").lower() == "true"
    )


@dataclass
class RetryConfig:
    """Retry configuration with exponential backoff."""
    mongodb_max_attempts: int = field(
        default_factory=lambda: int(os.environ.get("MONGODB_RETRY_MAX_ATTEMPTS", "3"))
    )
    mongodb_backoff_factor: float = field(
        default_factory=lambda: float(os.environ.get("MONGODB_RETRY_BACKOFF_FACTOR", "2.0"))
    )
    telegram_max_attempts: int = field(
        default_factory=lambda: int(os.environ.get("TELEGRAM_RETRY_MAX_ATTEMPTS", "3"))
    )
    telegram_backoff_factor: float = field(
        default_factory=lambda: float(os.environ.get("TELEGRAM_RETRY_BACKOFF_FACTOR", "2.0"))
    )
    api_max_attempts: int = field(
        default_factory=lambda: int(os.environ.get("API_RETRY_MAX_ATTEMPTS", "3"))
    )
    api_backoff_factor: float = field(
        default_factory=lambda: float(os.environ.get("API_RETRY_BACKOFF_FACTOR", "2.0"))
    )


@dataclass
class TimeoutConfig:
    """Timeout configuration (seconds)."""
    mongodb: int = field(
        default_factory=lambda: int(os.environ.get("MONGODB_TIMEOUT_SECONDS", "10"))
    )
    telegram: int = field(
        default_factory=lambda: int(os.environ.get("TELEGRAM_TIMEOUT_SECONDS", "10"))
    )
    api: int = field(
        default_factory=lambda: int(os.environ.get("API_TIMEOUT_SECONDS", "30"))
    )


@dataclass
class Config:
    """Global configuration."""
    signal: SignalConfig = field(default_factory=SignalConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)
    timeout: TimeoutConfig = field(default_factory=TimeoutConfig)


# Global config instance
config = Config()
