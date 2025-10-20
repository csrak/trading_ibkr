"""Trading models using Pydantic v2."""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field, ValidationInfo, field_validator


class OrderSide(str, Enum):
    """Order side enumeration."""

    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """Order type enumeration."""

    MARKET = "MKT"
    LIMIT = "LMT"
    STOP = "STP"
    STOP_LIMIT = "STP LMT"


class OrderStatus(str, Enum):
    """Order status enumeration."""

    PENDING = "PendingSubmit"
    SUBMITTED = "Submitted"
    FILLED = "Filled"
    CANCELLED = "Cancelled"
    REJECTED = "ApiRejected"


class SymbolContract(BaseModel):
    """Trading symbol/contract definition."""

    symbol: str = Field(..., description="Trading symbol")
    sec_type: str = Field(default="STK", description="Security type")
    exchange: str = Field(default="SMART", description="Exchange")
    currency: str = Field(default="USD", description="Currency")

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        """Ensure symbol is uppercase and non-empty."""
        if not v:
            raise ValueError("Symbol cannot be empty")
        return v.upper().strip()


class OrderRequest(BaseModel):
    """Order request model."""

    contract: SymbolContract
    side: OrderSide
    quantity: Annotated[int, Field(gt=0, description="Order quantity (must be positive)")]
    order_type: OrderType = Field(default=OrderType.MARKET)
    limit_price: Decimal | None = Field(default=None, description="Limit price for limit orders")
    stop_price: Decimal | None = Field(default=None, description="Stop price for stop orders")
    time_in_force: str | None = Field(default="DAY", description="Order time-in-force")
    transmit: bool = Field(default=True, description="Transmit order to market")
    expected_price: Decimal | None = Field(
        default=None, description="Estimated fill price used for risk validation"
    )

    @field_validator("limit_price")
    @classmethod
    def validate_limit_price(cls, v: Decimal | None, info: ValidationInfo) -> Decimal | None:
        """Validate limit price is provided for limit orders."""
        if info.data.get("order_type") in (OrderType.LIMIT, OrderType.STOP_LIMIT) and v is None:
            raise ValueError(f"Limit price required for {info.data.get('order_type')}")
        return v

    @field_validator("stop_price")
    @classmethod
    def validate_stop_price(cls, v: Decimal | None, info: ValidationInfo) -> Decimal | None:
        """Validate stop price is provided for stop orders."""
        if info.data.get("order_type") in (OrderType.STOP, OrderType.STOP_LIMIT) and v is None:
            raise ValueError(f"Stop price required for {info.data.get('order_type')}")
        return v


class OrderResult(BaseModel):
    """Order execution result."""

    order_id: int
    contract: SymbolContract
    side: OrderSide
    quantity: int
    order_type: OrderType
    status: OrderStatus
    filled_quantity: int = Field(default=0)
    avg_fill_price: Decimal = Field(default=Decimal("0"))
    commission: Decimal = Field(default=Decimal("0"))
    timestamp: datetime = Field(default_factory=datetime.now)
    parent_order_id: int | None = Field(default=None)
    child_order_ids: list[int] = Field(default_factory=list)


class Position(BaseModel):
    """Current position information."""

    contract: SymbolContract
    quantity: int = Field(..., description="Position size (positive=long, negative=short)")
    avg_cost: Decimal = Field(..., description="Average cost per share")
    market_value: Decimal = Field(..., description="Current market value")
    unrealized_pnl: Decimal = Field(..., description="Unrealized profit/loss")
    realized_pnl: Decimal = Field(default=Decimal("0"), description="Realized profit/loss")

    @property
    def is_long(self) -> bool:
        """Check if position is long."""
        return self.quantity > 0

    @property
    def is_short(self) -> bool:
        """Check if position is short."""
        return self.quantity < 0

    @property
    def avg_price(self) -> Decimal:
        """Alias for avg_cost to ease display formatting."""
        return self.avg_cost


class BracketOrderRequest(BaseModel):
    """Bracket order request (entry + stop loss + take profit).

    A bracket order consists of three orders:
    - Parent (entry): The initial order to enter a position (MARKET or LIMIT)
    - Stop loss: Automatic stop order to limit losses
    - Take profit: Automatic limit order to take profits

    When the parent order fills, both child orders are activated. If either
    child fills, the other is automatically cancelled.
    """

    parent: OrderRequest = Field(..., description="Entry order (parent)")
    stop_loss: OrderRequest = Field(..., description="Stop loss order (child)")
    take_profit: OrderRequest = Field(..., description="Take profit order (child)")

    @field_validator("stop_loss")
    @classmethod
    def validate_stop_loss_side(cls, v: OrderRequest, info: ValidationInfo) -> OrderRequest:
        """Ensure stop loss is on opposite side from parent."""
        parent = info.data.get("parent")
        if parent and v.side == parent.side:
            raise ValueError(
                f"Stop loss must be opposite side from parent: "
                f"parent={parent.side}, stop_loss={v.side}"
            )
        return v

    @field_validator("take_profit")
    @classmethod
    def validate_take_profit_side(cls, v: OrderRequest, info: ValidationInfo) -> OrderRequest:
        """Ensure take profit is on opposite side from parent."""
        parent = info.data.get("parent")
        if parent and v.side == parent.side:
            raise ValueError(
                f"Take profit must be opposite side from parent: "
                f"parent={parent.side}, take_profit={v.side}"
            )
        return v

    @field_validator("stop_loss")
    @classmethod
    def validate_stop_loss_quantity(cls, v: OrderRequest, info: ValidationInfo) -> OrderRequest:
        """Ensure stop loss quantity matches parent."""
        parent = info.data.get("parent")
        if parent and v.quantity != parent.quantity:
            raise ValueError(
                f"Stop loss quantity must match parent: "
                f"parent={parent.quantity}, stop_loss={v.quantity}"
            )
        return v

    @field_validator("take_profit")
    @classmethod
    def validate_take_profit_quantity(cls, v: OrderRequest, info: ValidationInfo) -> OrderRequest:
        """Ensure take profit quantity matches parent."""
        parent = info.data.get("parent")
        if parent and v.quantity != parent.quantity:
            raise ValueError(
                f"Take profit quantity must match parent: "
                f"parent={parent.quantity}, take_profit={v.quantity}"
            )
        return v

    @field_validator("stop_loss")
    @classmethod
    def validate_stop_loss_is_stop_order(cls, v: OrderRequest) -> OrderRequest:
        """Ensure stop loss is a STOP order."""
        if v.order_type not in (OrderType.STOP, OrderType.STOP_LIMIT):
            raise ValueError(f"Stop loss must be STOP or STOP_LIMIT, got {v.order_type}")
        return v

    @field_validator("take_profit")
    @classmethod
    def validate_take_profit_is_limit_order(cls, v: OrderRequest) -> OrderRequest:
        """Ensure take profit is a LIMIT order."""
        if v.order_type != OrderType.LIMIT:
            raise ValueError(f"Take profit must be LIMIT order, got {v.order_type}")
        return v


class TrailingStopConfig(BaseModel):
    """Trailing stop configuration.

    A trailing stop automatically adjusts the stop loss price as the market price
    moves favorably. For long positions, the stop loss rises with price increases.
    For short positions, the stop loss lowers with price decreases.

    The stop loss never moves against the position (never widens).
    """

    symbol: str = Field(..., description="Trading symbol")
    side: OrderSide = Field(..., description="SELL for long position, BUY for short position")
    quantity: Annotated[int, Field(gt=0, description="Position quantity")]
    trail_amount: Decimal | None = Field(
        default=None, description="Trailing amount in dollars (e.g., $5.00)"
    )
    trail_percent: Decimal | None = Field(
        default=None, description="Trailing percentage (e.g., 2.0 for 2%)"
    )
    activation_price: Decimal | None = Field(
        default=None, description="Optional activation threshold (start trailing above this price)"
    )

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        """Ensure symbol is uppercase and non-empty."""
        if not v:
            raise ValueError("Symbol cannot be empty")
        return v.upper().strip()

    @field_validator("trail_amount")
    @classmethod
    def validate_trail_amount_exclusive(
        cls, v: Decimal | None, info: ValidationInfo
    ) -> Decimal | None:
        """Ensure exactly one of trail_amount or trail_percent is set."""
        trail_percent = info.data.get("trail_percent")
        if v is None and trail_percent is None:
            raise ValueError("Either trail_amount or trail_percent must be specified")
        if v is not None and trail_percent is not None:
            raise ValueError("Cannot specify both trail_amount and trail_percent")
        if v is not None and v <= 0:
            raise ValueError(f"trail_amount must be positive, got {v}")
        return v

    @field_validator("trail_percent")
    @classmethod
    def validate_trail_percent(cls, v: Decimal | None) -> Decimal | None:
        """Validate trail_percent is in valid range."""
        if v is not None and (v <= 0 or v >= 100):
            raise ValueError(f"trail_percent must be between 0 and 100, got {v}")
        return v


class OCOOrderRequest(BaseModel):
    """One-Cancels-Other order pair.

    An OCO order consists of two orders where if one fills, the other is
    automatically cancelled. Useful for entering positions at different price
    levels or managing exits.

    Common use cases:
    - Enter long OR short (bracket around consolidation)
    - Take profit at target OR stop loss (alternative to bracket)
    - Scale out at multiple levels

    Note: IBKR uses native exchange OCO when possible. For manual OCO,
    there's a small race condition where both orders could fill in fast markets.
    """

    order_a: OrderRequest = Field(..., description="First order in OCO pair")
    order_b: OrderRequest = Field(..., description="Second order in OCO pair")
    group_id: str = Field(..., description="Unique identifier for this OCO pair")

    @field_validator("order_b")
    @classmethod
    def validate_same_symbol(cls, v: OrderRequest, info: ValidationInfo) -> OrderRequest:
        """Ensure both orders are for the same symbol."""
        order_a = info.data.get("order_a")
        if order_a and v.contract.symbol != order_a.contract.symbol:
            raise ValueError(
                f"Both orders must be for same symbol: "
                f"order_a={order_a.contract.symbol}, order_b={v.contract.symbol}"
            )
        return v

    @field_validator("order_b")
    @classmethod
    def validate_same_quantity(cls, v: OrderRequest, info: ValidationInfo) -> OrderRequest:
        """Ensure both orders have the same quantity."""
        order_a = info.data.get("order_a")
        if order_a and v.quantity != order_a.quantity:
            raise ValueError(
                f"Both orders must have same quantity: "
                f"order_a={order_a.quantity}, order_b={v.quantity}"
            )
        return v

    @field_validator("group_id")
    @classmethod
    def validate_group_id(cls, v: str) -> str:
        """Ensure group_id is non-empty."""
        if not v or not v.strip():
            raise ValueError("group_id cannot be empty")
        return v.strip()


class MarketData(BaseModel):
    """Real-time market data."""

    symbol: str
    timestamp: datetime
    bid: Decimal | None = Field(default=None)
    ask: Decimal | None = Field(default=None)
    last: Decimal | None = Field(default=None)
    volume: int = Field(default=0)

    @property
    def mid_price(self) -> Decimal | None:
        """Calculate mid price from bid/ask."""
        if self.bid is not None and self.ask is not None:
            return (self.bid + self.ask) / Decimal("2")
        return self.last
