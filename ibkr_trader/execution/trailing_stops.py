"""Trailing stop manager for dynamic stop loss adjustment.

This module provides automated trailing stop functionality that adjusts stop loss
orders as the market price moves favorably, while never widening the stop (moving
against the position).
"""

import asyncio
import json
from contextlib import suppress
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from loguru import logger

from ibkr_trader.events import EventBus, EventSubscription, EventTopic
from ibkr_trader.execution.broker import IBKRBroker
from ibkr_trader.models import (
    OrderRequest,
    OrderSide,
    OrderType,
    SymbolContract,
    TrailingStopConfig,
)


class TrailingStop:
    """Active trailing stop state."""

    def __init__(
        self,
        stop_id: str,
        config: TrailingStopConfig,
        order_id: int,
        current_stop_price: Decimal,
        high_water_mark: Decimal,
    ) -> None:
        self.stop_id = stop_id
        self.config = config
        self.order_id = order_id
        self.current_stop_price = current_stop_price
        self.high_water_mark = high_water_mark
        self.last_update_time: datetime = datetime.now(UTC)
        self.activated: bool = config.activation_price is None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for persistence."""
        return {
            "stop_id": self.stop_id,
            "config": {
                "symbol": self.config.symbol,
                "side": self.config.side.value,
                "quantity": self.config.quantity,
                "trail_amount": str(self.config.trail_amount) if self.config.trail_amount else None,
                "trail_percent": str(self.config.trail_percent)
                if self.config.trail_percent
                else None,
                "activation_price": str(self.config.activation_price)
                if self.config.activation_price
                else None,
            },
            "order_id": self.order_id,
            "current_stop_price": str(self.current_stop_price),
            "high_water_mark": str(self.high_water_mark),
            "activated": self.activated,
            "last_update_time": self.last_update_time.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TrailingStop":
        """Deserialize from dict."""
        config_data = data["config"]
        config = TrailingStopConfig(
            symbol=config_data["symbol"],
            side=OrderSide(config_data["side"]),
            quantity=config_data["quantity"],
            trail_amount=Decimal(config_data["trail_amount"])
            if config_data.get("trail_amount")
            else None,
            trail_percent=Decimal(config_data["trail_percent"])
            if config_data.get("trail_percent")
            else None,
            activation_price=Decimal(config_data["activation_price"])
            if config_data.get("activation_price")
            else None,
        )

        stop = cls(
            stop_id=data["stop_id"],
            config=config,
            order_id=data["order_id"],
            current_stop_price=Decimal(data["current_stop_price"]),
            high_water_mark=Decimal(data["high_water_mark"]),
        )
        stop.activated = data["activated"]
        stop.last_update_time = datetime.fromisoformat(data["last_update_time"])
        return stop


class TrailingStopManager:
    """Manages trailing stop orders with dynamic adjustment.

    Subscribes to market data events and automatically adjusts stop loss orders
    as prices move favorably. Implements rate limiting to avoid IBKR throttling.
    """

    def __init__(self, broker: IBKRBroker, event_bus: EventBus, state_file: Path) -> None:
        self.broker = broker
        self.event_bus = event_bus
        self.state_file = state_file
        self.active_stops: dict[str, TrailingStop] = {}
        self._subscription: EventSubscription | None = None
        self._rate_limiters: dict[str, datetime] = {}  # symbol -> last_update_time
        self._min_update_interval = 1.0  # seconds

        # Load persisted state
        self._load_state()

    async def start(self) -> None:
        """Start listening to market data events."""
        self._subscription = self.event_bus.subscribe(EventTopic.MARKET_DATA)
        # Start background task to process market data events
        self._event_task = asyncio.create_task(self._process_market_data_events())
        logger.info("TrailingStopManager started, {} active stops", len(self.active_stops))

    async def stop(self) -> None:
        """Stop listening to market data events."""
        if self._subscription:
            self._subscription.close()
        if hasattr(self, "_event_task"):
            self._event_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._event_task
        logger.info("TrailingStopManager stopped")

    async def _process_market_data_events(self) -> None:
        """Background task to process market data events from subscription."""
        if not self._subscription:
            return

        try:
            while True:
                event = await self._subscription.get()
                # Extract symbol and price from event
                # Event format depends on what's published to MARKET_DATA topic
                if hasattr(event, "symbol") and hasattr(event, "price"):
                    await self._on_market_data(event.symbol, event.price)
                elif hasattr(event, "symbol") and hasattr(event, "last"):
                    await self._on_market_data(event.symbol, event.last)
        except asyncio.CancelledError:
            logger.debug("Market data event processing cancelled")
            raise

    async def create_trailing_stop(self, config: TrailingStopConfig, initial_price: Decimal) -> str:
        """Create and activate a new trailing stop.

        Args:
            config: Trailing stop configuration
            initial_price: Current market price for calculating initial stop

        Returns:
            stop_id: Unique identifier for this trailing stop
        """
        # Calculate initial stop price
        if config.trail_amount:
            if config.side == OrderSide.SELL:  # Long position
                stop_price = initial_price - config.trail_amount
            else:  # Short position
                stop_price = initial_price + config.trail_amount
        else:  # trail_percent
            trail_decimal = config.trail_percent / Decimal("100")  # type: ignore
            if config.side == OrderSide.SELL:  # Long position
                stop_price = initial_price * (Decimal("1") - trail_decimal)
            else:  # Short position
                stop_price = initial_price * (Decimal("1") + trail_decimal)

        # Place initial stop loss order
        order_request = OrderRequest(
            contract=SymbolContract(symbol=config.symbol),
            side=config.side,
            quantity=config.quantity,
            order_type=OrderType.STOP,
            stop_price=stop_price,
            expected_price=stop_price,
        )

        result = await self.broker.place_order(order_request)
        stop_id = f"{config.symbol}_{result.order_id}"

        # Create trailing stop state
        trailing_stop = TrailingStop(
            stop_id=stop_id,
            config=config,
            order_id=result.order_id,
            current_stop_price=stop_price,
            high_water_mark=initial_price,
        )

        self.active_stops[stop_id] = trailing_stop
        self._save_state()

        logger.info(
            "Created trailing stop: {} for {} shares of {} at stop price {}",
            stop_id,
            config.quantity,
            config.symbol,
            stop_price,
        )

        return stop_id

    async def cancel_trailing_stop(self, stop_id: str) -> None:
        """Cancel and remove a trailing stop.

        Args:
            stop_id: Trailing stop identifier

        Raises:
            KeyError: If stop_id not found
        """
        if stop_id not in self.active_stops:
            raise KeyError(f"Trailing stop {stop_id} not found")

        # Cancel the stop loss order with IBKR
        # Note: broker doesn't have cancel_order yet, this is a placeholder
        logger.info("Cancelling trailing stop: {}", stop_id)

        # Remove from active stops
        del self.active_stops[stop_id]
        self._save_state()

        logger.info("Cancelled trailing stop: {}", stop_id)

    async def _on_market_data(self, symbol: str, price: Decimal) -> None:
        """Handle market data update.

        Args:
            symbol: Trading symbol
            price: Current market price
        """
        # Find all trailing stops for this symbol
        for stop_id, trailing_stop in list(self.active_stops.items()):
            if trailing_stop.config.symbol != symbol:
                continue

            # Check activation threshold
            if not trailing_stop.activated and trailing_stop.config.activation_price:
                if trailing_stop.config.side == OrderSide.SELL:  # Long position
                    if price >= trailing_stop.config.activation_price:
                        trailing_stop.activated = True
                        logger.info(
                            "Trailing stop {} activated at price {}",
                            stop_id,
                            price,
                        )
                else:  # Short position
                    if price <= trailing_stop.config.activation_price:
                        trailing_stop.activated = True
                        logger.info(
                            "Trailing stop {} activated at price {}",
                            stop_id,
                            price,
                        )

            if not trailing_stop.activated:
                continue

            # Update high water mark and calculate new stop price
            await self._update_stop_if_needed(trailing_stop, price)

    async def _update_stop_if_needed(self, trailing_stop: TrailingStop, price: Decimal) -> None:
        """Update stop loss if price moved favorably.

        Args:
            trailing_stop: Trailing stop to update
            price: Current market price
        """
        config = trailing_stop.config
        new_stop_price = None

        # For long positions (SELL stop)
        if config.side == OrderSide.SELL:
            if price > trailing_stop.high_water_mark:
                # Price increased, update high water mark
                trailing_stop.high_water_mark = price

                # Calculate new stop price
                if config.trail_amount:
                    new_stop_price = price - config.trail_amount
                else:  # trail_percent
                    trail_decimal = config.trail_percent / Decimal("100")  # type: ignore
                    new_stop_price = price * (Decimal("1") - trail_decimal)

        # For short positions (BUY stop)
        else:
            if price < trailing_stop.high_water_mark:
                # Price decreased, update high water mark
                trailing_stop.high_water_mark = price

                # Calculate new stop price
                if config.trail_amount:
                    new_stop_price = price + config.trail_amount
                else:  # trail_percent
                    trail_decimal = config.trail_percent / Decimal("100")  # type: ignore
                    new_stop_price = price * (Decimal("1") + trail_decimal)

        # Only update if new stop is better (never widen)
        if new_stop_price is not None and new_stop_price != trailing_stop.current_stop_price:
            if config.side == OrderSide.SELL:
                # Long: new stop should be higher
                if new_stop_price > trailing_stop.current_stop_price:
                    await self._modify_stop_order(trailing_stop, new_stop_price)
            else:
                # Short: new stop should be lower
                if new_stop_price < trailing_stop.current_stop_price:
                    await self._modify_stop_order(trailing_stop, new_stop_price)

    async def _modify_stop_order(
        self, trailing_stop: TrailingStop, new_stop_price: Decimal
    ) -> None:
        """Modify stop loss order with rate limiting.

        Args:
            trailing_stop: Trailing stop to modify
            new_stop_price: New stop price
        """
        symbol = trailing_stop.config.symbol

        # Check rate limit
        now = datetime.now(UTC)
        if symbol in self._rate_limiters:
            last_update = self._rate_limiters[symbol]
            time_since_update = (now - last_update).total_seconds()
            if time_since_update < self._min_update_interval:
                logger.debug(
                    "Rate limiting: skipping update for {} (last update {} sec ago)",
                    symbol,
                    time_since_update,
                )
                return

        # Note: broker doesn't have modify_order yet, this would be the interface:
        # await self.broker.modify_order(trailing_stop.order_id, stop_price=new_stop_price)

        # For now, log the intended modification
        logger.info(
            "Trailing stop {} updated: {} -> {}",
            trailing_stop.stop_id,
            trailing_stop.current_stop_price,
            new_stop_price,
        )

        # Update state
        trailing_stop.current_stop_price = new_stop_price
        trailing_stop.last_update_time = now
        self._rate_limiters[symbol] = now
        self._save_state()

    def _save_state(self) -> None:
        """Persist active trailing stops to disk."""
        state_data = {"stops": [stop.to_dict() for stop in self.active_stops.values()]}

        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(state_data, indent=2))

        logger.debug("Saved trailing stop state to {}", self.state_file)

    def _load_state(self) -> None:
        """Load persisted trailing stops from disk."""
        if not self.state_file.exists():
            logger.debug("No trailing stop state file found")
            return

        try:
            state_data = json.loads(self.state_file.read_text())
            for stop_dict in state_data.get("stops", []):
                trailing_stop = TrailingStop.from_dict(stop_dict)
                self.active_stops[trailing_stop.stop_id] = trailing_stop

            logger.info("Loaded {} trailing stops from state file", len(self.active_stops))
        except Exception as e:
            logger.error("Failed to load trailing stop state: {}", e)
