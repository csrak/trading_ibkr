"""Trading strategy base class and implementations."""

from abc import ABC, abstractmethod
from collections import deque
from decimal import Decimal

from loguru import logger
from pydantic import BaseModel, Field

from ibkr_trader.broker import IBKRBroker
from ibkr_trader.models import OrderRequest, OrderSide, OrderType, SymbolContract


class StrategyConfig(BaseModel):
    """Base configuration for trading strategies."""

    name: str = Field(..., description="Strategy name")
    symbols: list[str] = Field(..., description="List of symbols to trade")
    position_size: int = Field(default=10, description="Position size per trade")


class Strategy(ABC):
    """Base class for trading strategies.
    
    All strategies must implement on_bar() method for signal generation.
    """

    def __init__(self, config: StrategyConfig, broker: IBKRBroker) -> None:
        """Initialize strategy.
        
        Args:
            config: Strategy configuration
            broker: Broker interface
        """
        self.config = config
        self.broker = broker
        self._positions: dict[str, int] = {}

    @abstractmethod
    async def on_bar(self, symbol: str, price: Decimal) -> None:
        """Process new price bar.
        
        Args:
            symbol: Trading symbol
            price: Current price
        """
        pass

    async def get_position(self, symbol: str) -> int:
        """Get current position for symbol.
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Current position (positive=long, negative=short, 0=flat)
        """
        positions = await self.broker.get_positions()
        for pos in positions:
            if pos.contract.symbol == symbol:
                return pos.quantity
        return 0

    async def place_market_order(
        self, 
        symbol: str, 
        side: OrderSide, 
        quantity: int
    ) -> None:
        """Place a market order.
        
        Args:
            symbol: Trading symbol
            side: Order side (BUY/SELL)
            quantity: Order quantity
        """
        contract = SymbolContract(symbol=symbol)
        order_request = OrderRequest(
            contract=contract,
            side=side,
            quantity=quantity,
            order_type=OrderType.MARKET,
        )
        
        result = await self.broker.place_order(order_request)
        logger.info(
            f"Strategy '{self.config.name}': Placed {side.value} order for "
            f"{quantity} {symbol} (Order ID: {result.order_id})"
        )


class SMAConfig(StrategyConfig):
    """Configuration for Simple Moving Average strategy."""

    name: str = Field(default="SMA_Crossover")
    fast_period: int = Field(default=10, description="Fast SMA period")
    slow_period: int = Field(default=20, description="Slow SMA period")


class SimpleMovingAverageStrategy(Strategy):
    """Simple Moving Average crossover strategy.
    
    Generates BUY signal when fast SMA crosses above slow SMA.
    Generates SELL signal when fast SMA crosses below slow SMA.
    
    This is a simple test strategy for demonstration purposes.
    """

    def __init__(self, config: SMAConfig, broker: IBKRBroker) -> None:
        """Initialize SMA strategy.
        
        Args:
            config: SMA strategy configuration
            broker: Broker interface
        """
        super().__init__(config, broker)
        self.config: SMAConfig = config  # Type narrowing
        
        # Price history for each symbol
        self.price_history: dict[str, deque[Decimal]] = {
            symbol: deque(maxlen=self.config.slow_period)
            for symbol in config.symbols
        }
        
        # Track previous crossover state to detect changes
        self.prev_crossover: dict[str, bool | None] = {
            symbol: None for symbol in config.symbols
        }

    def _calculate_sma(self, prices: deque[Decimal], period: int) -> Decimal | None:
        """Calculate Simple Moving Average.
        
        Args:
            prices: Price history
            period: SMA period
            
        Returns:
            SMA value or None if insufficient data
        """
        if len(prices) < period:
            return None
        
        recent_prices = list(prices)[-period:]
        return sum(recent_prices) / Decimal(str(period))

    async def on_bar(self, symbol: str, price: Decimal) -> None:
        """Process new price bar and generate signals.
        
        Args:
            symbol: Trading symbol
            price: Current price
        """
        # Update price history
        if symbol not in self.price_history:
            self.price_history[symbol] = deque(maxlen=self.config.slow_period)
        
        self.price_history[symbol].append(price)
        
        # Calculate SMAs
        fast_sma = self._calculate_sma(
            self.price_history[symbol], 
            self.config.fast_period
        )
        slow_sma = self._calculate_sma(
            self.price_history[symbol], 
            self.config.slow_period
        )
        
        # Need both SMAs to generate signals
        if fast_sma is None or slow_sma is None:
            logger.debug(
                f"Insufficient data for {symbol}: "
                f"fast_sma={'N/A' if fast_sma is None else fast_sma:.2f}, "
                f"slow_sma={'N/A' if slow_sma is None else slow_sma:.2f}"
            )
            return
        
        # Detect crossover
        fast_above_slow = fast_sma > slow_sma
        prev_crossover = self.prev_crossover.get(symbol)
        
        # Get current position
        current_position = await self.get_position(symbol)
        
        # Generate signals on crossover
        if prev_crossover is not None:
            # Bullish crossover: fast crosses above slow
            if fast_above_slow and not prev_crossover and current_position <= 0:
                logger.info(
                    f"📈 BULLISH CROSSOVER detected for {symbol}: "
                    f"Fast SMA ({fast_sma:.2f}) > Slow SMA ({slow_sma:.2f})"
                )
                await self.place_market_order(
                    symbol=symbol,
                    side=OrderSide.BUY,
                    quantity=self.config.position_size,
                )
            
            # Bearish crossover: fast crosses below slow
            elif not fast_above_slow and prev_crossover and current_position >= 0:
                logger.info(
                    f"📉 BEARISH CROSSOVER detected for {symbol}: "
                    f"Fast SMA ({fast_sma:.2f}) < Slow SMA ({slow_sma:.2f})"
                )
                await self.place_market_order(
                    symbol=symbol,
                    side=OrderSide.SELL,
                    quantity=self.config.position_size,
                )
        
        # Update crossover state
        self.prev_crossover[symbol] = fast_above_slow
        
        logger.debug(
            f"SMA Update for {symbol}: Price={price:.2f}, "
            f"Fast={fast_sma:.2f}, Slow={slow_sma:.2f}, "
            f"Position={current_position}"
        )