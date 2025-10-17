# Order Book (L2 Market Depth) Implementation Guide

## Overview

This document describes how to implement Level 2 (L2) market depth data streaming from IBKR for advanced market making and microstructure strategies.

## IBKR API Structure

### Key Methods

**1. Request Market Depth (L2 Order Book)**
```python
# From ib_insync library
ticker = ib.reqMktDepth(
    contract,           # Qualified contract
    numRows=5,         # Number of levels (max 5 for most stocks, 10 for futures)
    isSmartDepth=False, # False = use direct exchange feed
    mktDepthOptions=[]  # Additional options
)
```

**2. Update Events**
```python
# ib_insync provides these events on the ticker object:
ticker.updateEvent      # Fired on any update
ticker.domBidsUpdateEvent  # Fired on bid side updates
ticker.domAsksUpdateEvent  # Fired on ask side updates
```

**3. Data Structure**
```python
# ticker.domBids and ticker.domAsks return lists of DOMLevel objects:
class DOMLevel:
    price: float         # Price level
    size: float          # Aggregate size at this level
    marketMaker: str     # Market maker ID (if available)
```

## Our Data Model

### Current Implementation

Located in `model/data/models.py`:

```python
@dataclass(slots=True)
class BookSide(str, Enum):
    BID = "bid"
    ASK = "ask"

@dataclass(slots=True)
class OrderBookLevel:
    side: BookSide       # BID or ASK
    price: float         # Price level
    size: float          # Aggregate size
    level: int           # Level index (0 = best, 1 = second best, etc.)
    num_orders: int | None = None  # Number of orders (if available)

@dataclass(slots=True)
class OrderBookSnapshot:
    timestamp: datetime
    symbol: str
    levels: list[OrderBookLevel]  # All bid/ask levels combined
    venue: str | None = None       # Exchange/venue identifier
```

## Implementation Plan

### Phase 1: Add Order Book Service (Step 5)

**File:** `ibkr_trader/order_book_service.py`

```python
from datetime import UTC, datetime
from ib_insync import IB, Contract, Ticker
from loguru import logger

from ibkr_trader.events import EventBus, EventTopic
from model.data.models import BookSide, OrderBookLevel, OrderBookSnapshot


class OrderBookEvent:
    """Event published when order book updates."""

    def __init__(self, snapshot: OrderBookSnapshot):
        self.snapshot = snapshot
        self.timestamp = snapshot.timestamp


class OrderBookService:
    """Manage L2 market depth subscriptions."""

    def __init__(self, event_bus: EventBus, num_levels: int = 5):
        self._event_bus = event_bus
        self._num_levels = num_levels
        self._ib: IB | None = None
        self._subscriptions: dict[str, Ticker] = {}

    def attach_ib(self, ib: IB) -> None:
        """Attach IBKR connection."""
        self._ib = ib

    async def subscribe(self, contract: Contract, symbol: str) -> None:
        """Subscribe to order book updates for a contract."""
        if self._ib is None:
            raise RuntimeError("IB connection not attached")

        if symbol in self._subscriptions:
            logger.warning(f"Already subscribed to order book for {symbol}")
            return

        qualified = await self._ib.qualifyContractsAsync(contract)
        if not qualified:
            raise ValueError(f"Unable to qualify contract: {symbol}")

        contract = qualified[0]
        ticker = self._ib.reqMktDepth(contract, numRows=self._num_levels)

        # Register callbacks
        loop = asyncio.get_running_loop()

        def _on_update(ticker: Ticker) -> None:
            loop.create_task(self._publish_snapshot(symbol, ticker))

        ticker.domBidsUpdateEvent += _on_update
        ticker.domAsksUpdateEvent += _on_update

        self._subscriptions[symbol] = ticker
        logger.info(f"Subscribed to L2 order book: {symbol} ({self._num_levels} levels)")

    async def unsubscribe(self, symbol: str) -> None:
        """Unsubscribe from order book updates."""
        ticker = self._subscriptions.pop(symbol, None)
        if ticker and self._ib:
            self._ib.cancelMktDepth(ticker.contract)
            logger.info(f"Unsubscribed from order book: {symbol}")

    async def _publish_snapshot(self, symbol: str, ticker: Ticker) -> None:
        """Convert IBKR DOMLevel data to OrderBookSnapshot and publish."""
        levels: list[OrderBookLevel] = []

        # Convert bids
        for level_idx, dom_level in enumerate(ticker.domBids):
            if dom_level.price > 0:  # Valid price
                levels.append(OrderBookLevel(
                    side=BookSide.BID,
                    price=dom_level.price,
                    size=dom_level.size,
                    level=level_idx,
                ))

        # Convert asks
        for level_idx, dom_level in enumerate(ticker.domAsks):
            if dom_level.price > 0:  # Valid price
                levels.append(OrderBookLevel(
                    side=BookSide.ASK,
                    price=dom_level.price,
                    size=dom_level.size,
                    level=level_idx,
                ))

        if not levels:
            return  # No valid data yet

        snapshot = OrderBookSnapshot(
            timestamp=datetime.now(UTC),
            symbol=symbol,
            levels=levels,
            venue="IBKR",
        )

        event = OrderBookEvent(snapshot)
        await self._event_bus.publish(EventTopic.ORDER_BOOK, event)
```

### Phase 2: Add EventTopic.ORDER_BOOK

**File:** `ibkr_trader/events.py`

Add to EventTopic enum:
```python
class EventTopic(str, Enum):
    MARKET_DATA = "market_data"
    ORDER_STATUS = "order_status"
    EXECUTION = "execution"
    DIAGNOSTIC = "diagnostic"
    ORDER_BOOK = "order_book"  # NEW
```

### Phase 3: Update ConfigBasedLiveStrategy

**File:** `ibkr_trader/strategy_adapters.py`

Add order book subscription and forwarding:

```python
class ConfigBasedLiveStrategy(Strategy):
    """Wrapper that adapts config-based replay strategies to live execution."""

    def __init__(
        self,
        impl: BaseStrategy,
        broker: BrokerProtocol,
        event_bus: EventBus,
        symbol: str,
        enable_order_book: bool = False,  # NEW
    ) -> None:
        config = StrategyConfig(name=type(impl).__name__, symbols=[symbol])
        super().__init__(config=config, broker=broker, event_bus=event_bus)
        self.impl = impl
        self._symbol = symbol
        self._enable_order_book = enable_order_book
        self._order_book_subscription = None

    async def start(self) -> None:
        """Start strategy and optionally subscribe to order book."""
        await super().start()

        if self._enable_order_book:
            # Subscribe to order book events
            self._order_book_subscription = self._event_bus.subscribe(EventTopic.ORDER_BOOK)
            asyncio.create_task(self._process_order_book_events())

    async def stop(self) -> None:
        """Stop strategy and cleanup subscriptions."""
        # Cancel order book processing
        # ... cleanup logic
        await super().stop()

    async def _process_order_book_events(self) -> None:
        """Forward order book events to strategy."""
        if self._order_book_subscription is None:
            return

        async for event in self._order_book_subscription:
            if hasattr(event, 'snapshot'):
                await self.impl.on_order_book(event.snapshot, self._broker)
```

## Usage Example

```python
# In CLI or application code:
from ibkr_trader.order_book_service import OrderBookService

# Initialize
order_book_service = OrderBookService(event_bus, num_levels=5)
order_book_service.attach_ib(broker.ib)

# Subscribe for a strategy
await order_book_service.subscribe(contract, "AAPL")

# Strategy receives updates via on_order_book() callback
```

## Testing Strategy

### Unit Tests
- Test `OrderBookService` converts IBKR DOMLevel to our format
- Test event publishing
- Test subscription/unsubscription lifecycle

### Integration Tests
- Test with mock IB client returning fake order book data
- Verify strategy receives callbacks correctly
- Test multiple symbol subscriptions

### Live Paper Trading
- Test with real IBKR paper account
- Verify data quality and update frequency
- Monitor for API throttling or errors

## Limitations & Considerations

1. **IBKR Limits:**
   - Most stocks: 5 levels max
   - Futures: 10 levels max
   - Some exchanges don't support depth data

2. **Performance:**
   - High update frequency (100+ updates/second possible)
   - Need efficient snapshot diffing for strategies
   - Consider sampling/throttling for some use cases

3. **Market Data Fees:**
   - L2 data may require additional subscription fees
   - Check IBKR market data subscriptions before enabling

4. **Venue Coverage:**
   - Not all exchanges provide full depth
   - Some only provide top-of-book (BBO)

## Next Steps

1. ✅ Document IBKR API structure (this file)
2. ⬜ Implement `OrderBookService` skeleton
3. ⬜ Add `EventTopic.ORDER_BOOK` to events
4. ⬜ Update `ConfigBasedLiveStrategy` with order book support
5. ⬜ Add unit tests for order book conversion
6. ⬜ Add integration test with mock IB client
7. ⬜ Test with paper trading account
8. ⬜ Document market data subscription requirements

## References

- [ib_insync Documentation](https://ib-insync.readthedocs.io/)
- [IBKR Market Depth API](https://interactivebrokers.github.io/tws-api/market_depth.html)
- [Order Book Data Model](../model/data/models.py)
