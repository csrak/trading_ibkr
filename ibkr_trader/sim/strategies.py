"""Sample strategies for replay simulations."""

from __future__ import annotations

from decimal import Decimal

from ibkr_trader.base_strategy import BrokerProtocol
from ibkr_trader.models import OrderRequest, OrderSide, OrderType, SymbolContract
from ibkr_trader.sim.runner import ReplayStrategy
from model.data.models import OrderBookSnapshot


class FixedSpreadMMStrategy(ReplayStrategy):
    """Minimal market-making strategy that posts symmetric quotes around mid."""

    def __init__(
        self,
        symbol: str,
        quote_size: int = 1,
        spread: float = 0.1,
        inventory_limit: int = 5,
    ) -> None:
        self.symbol = symbol
        self.quote_size = quote_size
        self.spread = spread
        self.inventory_limit = inventory_limit
        self.inventory = 0
        self.fills = 0
        self.total_filled_qty = 0
        self.active_bid_id: int | None = None
        self.active_ask_id: int | None = None

    async def on_order_book(self, snapshot: OrderBookSnapshot, broker: BrokerProtocol) -> None:
        if snapshot.symbol != self.symbol or not snapshot.levels:
            return
        best_bid = max(
            (level.price for level in snapshot.levels if level.side.value == "bid"), default=None
        )
        best_ask = min(
            (level.price for level in snapshot.levels if level.side.value == "ask"), default=None
        )
        if best_bid is None or best_ask is None:
            return

        mid = (best_bid + best_ask) / 2
        bid_price = max(0.01, mid - self.spread / 2)
        ask_price = mid + self.spread / 2

        # Cancel existing quotes (no modify support yet)
        if self.active_bid_id is not None:
            await broker.cancel_order(self.active_bid_id)
            self.active_bid_id = None
        if self.active_ask_id is not None:
            await broker.cancel_order(self.active_ask_id)
            self.active_ask_id = None

        # Skip bids if inventory too long, skip asks if too short
        if self.inventory < self.inventory_limit:
            bid_request = OrderRequest(
                contract=SymbolContract(symbol=self.symbol),
                side=OrderSide.BUY,
                quantity=self.quote_size,
                order_type=OrderType.LIMIT,
                limit_price=Decimal(str(round(bid_price, 2))),
            )
            result = await broker.submit_limit_order(bid_request)
            self.active_bid_id = result.order_id

        if self.inventory > -self.inventory_limit:
            ask_request = OrderRequest(
                contract=SymbolContract(symbol=self.symbol),
                side=OrderSide.SELL,
                quantity=self.quote_size,
                order_type=OrderType.LIMIT,
                limit_price=Decimal(str(round(ask_price, 2))),
            )
            result = await broker.submit_limit_order(ask_request)
            self.active_ask_id = result.order_id

    async def on_fill(self, side: OrderSide, quantity: int) -> None:
        self.fills += 1
        self.total_filled_qty += quantity
        self.inventory += quantity if side == OrderSide.BUY else -quantity
