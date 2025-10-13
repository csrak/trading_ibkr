"""Trading models using Pydantic v2."""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field, field_validator


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
    def validate_limit_price(cls, v: Decimal | None, info: dict) -> Decimal | None:
        """Validate limit price is provided for limit orders."""
        if info.data.get("order_type") in (OrderType.LIMIT, OrderType.STOP_LIMIT) and v is None:
            raise ValueError(f"Limit price required for {info.data.get('order_type')}")
        return v

    @field_validator("stop_price")
    @classmethod
    def validate_stop_price(cls, v: Decimal | None, info: dict) -> Decimal | None:
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
