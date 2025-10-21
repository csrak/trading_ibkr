"""Configuration management for IBKR Personal Trader."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from ibkr_trader.risk.fees import FeeConfig


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
        default=TradingMode.PAPER, description="Trading mode - paper or live"
    )

    # Safety settings
    max_position_size: int = Field(default=100, description="Maximum position size per symbol")
    max_daily_loss: float = Field(default=1000.0, description="Maximum daily loss in USD")
    max_order_exposure: float = Field(
        default=10000.0, description="Maximum notional exposure per single order"
    )
    use_mock_market_data: bool = Field(
        default=True,
        description="Generate mock market data instead of streaming from IBKR",
    )
    max_correlated_exposure: float | None = Field(
        default=None,
        description="Maximum combined exposure (USD) across highly correlated symbols",
    )
    correlation_threshold: float = Field(
        default=0.75,
        description="Correlation coefficient threshold for aggregating exposures",
    )

    # Fee and slippage configuration
    enable_fee_estimates: bool = Field(
        default=False,
        description="Enable fee and slippage estimates in risk calculations",
    )
    stock_commission_per_share: float = Field(
        default=0.005,
        description="Stock commission per share (IBKR Tiered: $0.005)",
    )
    stock_commission_minimum: float = Field(
        default=1.00,
        description="Minimum stock commission per order",
    )
    stock_slippage_bps: float = Field(
        default=5.0,
        description="Stock slippage estimate in basis points (default: 5 bps)",
    )
    forex_commission_percentage: float = Field(
        default=0.00002,
        description="FX commission as percentage of notional (IBKR: 0.2 bps = 0.00002)",
    )
    forex_slippage_bps: float = Field(
        default=1.0,
        description="FX slippage estimate in basis points (default: 1 bp, highly liquid)",
    )
    option_commission_per_contract: float = Field(
        default=0.65,
        description="Option commission per contract (IBKR: $0.65-$1.00)",
    )
    option_slippage_bps: float = Field(
        default=20.0,
        description="Option slippage estimate in basis points (default: 20 bps)",
    )

    # Data paths
    data_dir: Path = Field(default=Path("data"), description="Directory for storing data")
    log_dir: Path = Field(default=Path("logs"), description="Directory for logs")
    training_cache_dir: Path = Field(
        default=Path("data/cache"),
        description="Cache directory for model training datasets",
    )

    # Training data defaults
    training_data_source: str = Field(
        default="yfinance",
        description="Default data source identifier for training jobs",
    )
    training_client_id: int = Field(
        default=190,
        description="IBKR client ID used for historical data snapshots during training",
    )
    training_max_snapshots: int = Field(
        default=50,
        description="Maximum IBKR historical data requests per training session",
    )
    training_snapshot_interval: float = Field(
        default=1.0,
        description="Minimum seconds between IBKR historical requests during training",
    )
    training_price_cache_ttl: float | None = Field(
        default=3600.0,
        description="TTL in seconds for cached price bars (None disables TTL)",
    )
    training_option_cache_ttl: float | None = Field(
        default=3600.0,
        description="TTL in seconds for cached option chains (None disables TTL)",
    )

    @field_validator("port")
    @classmethod
    def validate_port_matches_mode(cls, v: int, info: dict) -> int:
        """Ensure port matches trading mode."""
        if v == 7496:
            # Live trading port
            pass  # Will be caught by LiveTradingGuard
        return v

    @field_validator("correlation_threshold")
    @classmethod
    def validate_correlation_threshold(cls, value: float) -> float:
        """Ensure correlation threshold is in a valid range."""
        if not (0.0 < value <= 1.0):
            raise ValueError("correlation_threshold must be between 0 and 1 (exclusive of 0).")
        return value

    def model_post_init(self, __context: object) -> None:
        """Create directories after initialization."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.training_cache_dir.mkdir(parents=True, exist_ok=True)

    def create_fee_config(self) -> FeeConfig:
        """Create FeeConfig from settings.

        Returns:
            FeeConfig instance with commission and slippage profiles from config.
        """
        from decimal import Decimal

        from ibkr_trader.risk.fees import CommissionProfile, FeeConfig, SlippageEstimate

        return FeeConfig(
            stock_commission=CommissionProfile(
                per_share=Decimal(str(self.stock_commission_per_share)),
                minimum=Decimal(str(self.stock_commission_minimum)),
            ),
            forex_commission=CommissionProfile(
                percentage=Decimal(str(self.forex_commission_percentage)),
            ),
            option_commission=CommissionProfile(
                per_share=Decimal(str(self.option_commission_per_contract)),
                minimum=Decimal(str(self.stock_commission_minimum)),
            ),
            stock_slippage=SlippageEstimate(
                basis_points=Decimal(str(self.stock_slippage_bps)),
            ),
            forex_slippage=SlippageEstimate(
                basis_points=Decimal(str(self.forex_slippage_bps)),
            ),
            option_slippage=SlippageEstimate(
                basis_points=Decimal(str(self.option_slippage_bps)),
            ),
        )


def load_config() -> IBKRConfig:
    """Load configuration from environment and .env file."""
    return IBKRConfig()
