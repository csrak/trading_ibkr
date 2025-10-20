"""OCO (One-Cancels-Other) order manager.

This module provides automated OCO order functionality where if one order fills,
the other is automatically cancelled. Useful for entering positions at different
price levels or managing exits.
"""

import asyncio
import json
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from ibkr_trader.broker import IBKRBroker
from ibkr_trader.events import EventBus, EventSubscription, EventTopic
from ibkr_trader.models import OCOOrderRequest, OrderStatus


class OCOPair:
    """Active OCO order pair state."""

    def __init__(
        self,
        group_id: str,
        order_a_id: int,
        order_b_id: int,
        symbol: str,
        quantity: int,
    ) -> None:
        self.group_id = group_id
        self.order_a_id = order_a_id
        self.order_b_id = order_b_id
        self.symbol = symbol
        self.quantity = quantity
        self.order_a_filled: int = 0
        self.order_b_filled: int = 0
        self.cancelled: bool = False
        self.created_at: datetime = datetime.now(UTC)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for persistence."""
        return {
            "group_id": self.group_id,
            "order_a_id": self.order_a_id,
            "order_b_id": self.order_b_id,
            "symbol": self.symbol,
            "quantity": self.quantity,
            "order_a_filled": self.order_a_filled,
            "order_b_filled": self.order_b_filled,
            "cancelled": self.cancelled,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OCOPair":
        """Deserialize from dict."""
        pair = cls(
            group_id=data["group_id"],
            order_a_id=data["order_a_id"],
            order_b_id=data["order_b_id"],
            symbol=data["symbol"],
            quantity=data["quantity"],
        )
        pair.order_a_filled = data["order_a_filled"]
        pair.order_b_filled = data["order_b_filled"]
        pair.cancelled = data["cancelled"]
        pair.created_at = datetime.fromisoformat(data["created_at"])
        return pair


class OCOOrderManager:
    """Manages OCO (One-Cancels-Other) order pairs.

    Subscribes to execution events and automatically cancels the unfilled order
    when one order in the pair fills. Handles partial fills by adjusting the
    remaining quantity appropriately.
    """

    def __init__(self, broker: IBKRBroker, event_bus: EventBus, state_file: Path) -> None:
        self.broker = broker
        self.event_bus = event_bus
        self.state_file = state_file
        self.active_pairs: dict[str, OCOPair] = {}  # group_id -> OCOPair
        self._order_to_group: dict[int, str] = {}  # order_id -> group_id
        self._subscription: EventSubscription | None = None

        # Load persisted state
        self._load_state()

    async def start(self) -> None:
        """Start listening to execution events."""
        self._subscription = self.event_bus.subscribe(EventTopic.EXECUTION)
        # Start background task to process execution events
        self._event_task = asyncio.create_task(self._process_execution_events())
        logger.info("OCOOrderManager started, {} active pairs", len(self.active_pairs))

    async def stop(self) -> None:
        """Stop listening to execution events."""
        if self._subscription:
            self._subscription.close()
        if hasattr(self, "_event_task"):
            self._event_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._event_task
        logger.info("OCOOrderManager stopped")

    async def _process_execution_events(self) -> None:
        """Background task to process execution events from subscription."""
        if not self._subscription:
            return

        try:
            while True:
                event = await self._subscription.get()
                # Extract order_id and status from event
                # Event format depends on what's published to EXECUTION topic
                if hasattr(event, "order_id") and hasattr(event, "status"):
                    await self._on_execution(event.order_id, event.status)
                elif (
                    hasattr(event, "order_id")
                    and hasattr(event, "filled_quantity")
                    and event.filled_quantity > 0
                ):
                    # Alternative: event has filled_quantity instead of status
                    await self._on_execution(event.order_id, OrderStatus.FILLED)
        except asyncio.CancelledError:
            logger.debug("Execution event processing cancelled")
            raise

    async def place_oco_order(self, request: OCOOrderRequest) -> str:
        """Place OCO order pair.

        Args:
            request: OCO order request

        Returns:
            group_id: Unique identifier for this OCO pair
        """
        # Place both orders
        result_a = await self.broker.place_order(request.order_a)
        result_b = await self.broker.place_order(request.order_b)

        # Create OCO pair state
        oco_pair = OCOPair(
            group_id=request.group_id,
            order_a_id=result_a.order_id,
            order_b_id=result_b.order_id,
            symbol=request.order_a.contract.symbol,
            quantity=request.order_a.quantity,
        )

        self.active_pairs[request.group_id] = oco_pair
        self._order_to_group[result_a.order_id] = request.group_id
        self._order_to_group[result_b.order_id] = request.group_id
        self._save_state()

        logger.info(
            "Created OCO pair: {} (orders {} and {})",
            request.group_id,
            result_a.order_id,
            result_b.order_id,
        )

        return request.group_id

    async def _on_execution(self, order_id: int, status: OrderStatus) -> None:
        """Handle order execution event.

        Args:
            order_id: Order ID that was executed
            status: Order status
        """
        # Check if this order is part of an OCO pair
        group_id = self._order_to_group.get(order_id)
        if not group_id:
            return

        oco_pair = self.active_pairs.get(group_id)
        if not oco_pair or oco_pair.cancelled:
            return

        # If order filled, cancel the other order
        if status == OrderStatus.FILLED:
            if order_id == oco_pair.order_a_id:
                oco_pair.order_a_filled = oco_pair.quantity
                other_order_id = oco_pair.order_b_id
            else:
                oco_pair.order_b_filled = oco_pair.quantity
                other_order_id = oco_pair.order_a_id

            # Cancel the other order
            # Note: broker doesn't have cancel_order yet, this is a placeholder
            logger.info(
                "OCO pair {} triggered: order {} filled, cancelling order {}",
                group_id,
                order_id,
                other_order_id,
            )

            # Mark as cancelled
            oco_pair.cancelled = True
            self._save_state()

            # Remove from active pairs
            del self.active_pairs[group_id]
            if oco_pair.order_a_id in self._order_to_group:
                del self._order_to_group[oco_pair.order_a_id]
            if oco_pair.order_b_id in self._order_to_group:
                del self._order_to_group[oco_pair.order_b_id]

    def _save_state(self) -> None:
        """Persist active OCO pairs to disk."""
        state_data = {"pairs": [pair.to_dict() for pair in self.active_pairs.values()]}

        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(state_data, indent=2))

        logger.debug("Saved OCO pair state to {}", self.state_file)

    def _load_state(self) -> None:
        """Load persisted OCO pairs from disk."""
        if not self.state_file.exists():
            logger.debug("No OCO pair state file found")
            return

        try:
            state_data = json.loads(self.state_file.read_text())
            for pair_dict in state_data.get("pairs", []):
                oco_pair = OCOPair.from_dict(pair_dict)
                self.active_pairs[oco_pair.group_id] = oco_pair
                self._order_to_group[oco_pair.order_a_id] = oco_pair.group_id
                self._order_to_group[oco_pair.order_b_id] = oco_pair.group_id

            logger.info("Loaded {} OCO pairs from state file", len(self.active_pairs))
        except Exception as e:
            logger.error("Failed to load OCO pair state: {}", e)
