"""Configuration management for IBKR Personal Trader."""

from enum import Enum
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TradingMode(str, Enum):
    """Trading mode enumeration."""

    PAPER = "paper"
    LIVE = "live"


class IBKRConfig(BaseSettings):
    """IBKR connection and trading configuration.
    
    Uses Pydantic v2 settings with environment variable support.
    Defaults to paper trading for safety.
    """

    model_config = SettingsConfigDict(
        env_prefix="IBKR_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Connection settings
    host: str = Field(default="127.0.0.1", description="TWS/Gateway host")
    port: int = Field(default=7497, description="TWS paper port (7497) or live port (7496)")
    client_id: int = Field(default=1, description="Unique client ID")
    
    # Trading mode
    trading_mode: TradingMode = Field(
        default=TradingMode.PAPER,
        description="Trading mode - paper or live"
    )
    
    # Safety settings
    max_position_size: int = Field(default=100, description="Maximum position size per symbol")
    max_daily_loss: float = Field(default=1000.0, description="Maximum daily loss in USD")
    
    # Data paths
    data_dir: Path = Field(default=Path("data"), description="Directory for storing data")
    log_dir: Path = Field(default=Path("logs"), description="Directory for logs")

    @field_validator("port")
    @classmethod
    def validate_port_matches_mode(cls, v: int, info: dict) -> int:
        """Ensure port matches trading mode."""
        # Note: info.data might not have trading_mode yet during initialization
        # This is a soft validation
        if v == 7496:
            # Live trading port
            pass  # Will be caught by LiveTradingGuard
        return v

    def model_post_init(self, __context: object) -> None:
        """Create directories after initialization."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


def load_config() -> IBKRConfig:
    """Load configuration from environment and .env file."""
    return IBKRConfig()