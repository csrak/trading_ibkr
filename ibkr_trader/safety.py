"""Trading safety guards and validation."""

from typing import NoReturn

from loguru import logger

from ibkr_trader.config import IBKRConfig, TradingMode


class LiveTradingError(Exception):
    """Raised when live trading safeguards are violated."""


class LiveTradingGuard:
    """Guards against accidental live trading.

    This class enforces that live trading can only occur when:
    1. Explicitly enabled via --live CLI flag
    2. Configuration is set to live mode
    3. User confirms with explicit acknowledgment
    """

    def __init__(self, config: IBKRConfig, live_flag_enabled: bool = False) -> None:
        """Initialize the guard.

        Args:
            config: IBKR configuration
            live_flag_enabled: Whether --live flag was passed via CLI
        """
        self.config = config
        self.live_flag_enabled = live_flag_enabled
        self._live_acknowledged = False

    def validate_trading_mode(self) -> None:
        """Validate that trading mode is safe to proceed.

        Raises:
            LiveTradingError: If live trading conditions are not met
        """
        if self.config.trading_mode == TradingMode.LIVE:
            if not self.live_flag_enabled:
                self._raise_live_trading_error(
                    "Live trading mode detected but --live flag not provided"
                )

            if not self._live_acknowledged:
                self._raise_live_trading_error("Live trading must be explicitly acknowledged")

            # Additional validation for live port
            if self.config.port == 7497:
                logger.warning(
                    "Trading mode is LIVE but port is 7497 (paper port). Check your configuration."
                )

        logger.info(f"Trading mode validated: {self.config.trading_mode.value}")

    def acknowledge_live_trading(self) -> None:
        """Acknowledge live trading after user confirmation.

        This should only be called after explicit user confirmation.
        """
        if self.config.trading_mode == TradingMode.LIVE and self.live_flag_enabled:
            self._live_acknowledged = True
            logger.warning("Live trading acknowledged - real money at risk")
        else:
            logger.info("Paper trading mode - no real money at risk")

    def check_order_safety(self, symbol: str, quantity: int) -> None:
        """Validate order parameters against safety limits.

        Args:
            symbol: Trading symbol
            quantity: Order quantity

        Raises:
            LiveTradingError: If order exceeds safety limits
        """
        if abs(quantity) > self.config.max_position_size:
            raise LiveTradingError(
                f"Order quantity {quantity} exceeds max position size "
                f"{self.config.max_position_size} for {symbol}"
            )

    def _raise_live_trading_error(self, message: str) -> NoReturn:
        """Raise a live trading error with standard formatting.

        Args:
            message: Error message

        Raises:
            LiveTradingError: Always
        """
        error_msg = (
            f"{message}\n"
            "To enable live trading:\n"
            "1. Set IBKR_TRADING_MODE=live in your environment\n"
            "2. Pass --live flag when running the CLI\n"
            "3. Explicitly acknowledge live trading when prompted\n"
            "Live trading involves real money - use with extreme caution"
        )
        logger.error(error_msg)
        raise LiveTradingError(error_msg)

    @property
    def is_paper_trading(self) -> bool:
        """Check if currently in paper trading mode."""
        return self.config.trading_mode == TradingMode.PAPER

    @property
    def is_live_trading(self) -> bool:
        """Check if currently in live trading mode."""
        return (
            self.config.trading_mode == TradingMode.LIVE
            and self.live_flag_enabled
            and self._live_acknowledged
        )
