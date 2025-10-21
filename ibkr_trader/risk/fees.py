"""Fee and slippage estimation for risk calculations."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from ibkr_trader.models import OrderSide, SymbolContract


class CommissionProfile(BaseModel):
    """Commission structure for an asset class."""

    per_share: Decimal = Field(
        default=Decimal("0"),
        description="Per-share commission (e.g., $0.005 for US stocks)",
    )
    minimum: Decimal = Field(
        default=Decimal("0"),
        description="Minimum commission per order",
    )
    maximum: Decimal = Field(
        default=Decimal("0"),
        description="Maximum commission per order (0 = no cap)",
    )
    percentage: Decimal = Field(
        default=Decimal("0"),
        description="Percentage of notional (e.g., 0.0020 for FX)",
    )

    def calculate(self, quantity: int, price: Decimal) -> Decimal:
        """Calculate estimated commission for an order.

        Args:
            quantity: Number of shares/contracts
            price: Price per unit

        Returns:
            Estimated commission cost
        """
        # Per-share component
        commission = self.per_share * Decimal(abs(quantity))

        # Percentage component
        if self.percentage > 0:
            notional = Decimal(abs(quantity)) * price
            commission += notional * self.percentage

        # Apply minimum
        if self.minimum > 0:
            commission = max(commission, self.minimum)

        # Apply maximum cap
        if self.maximum > 0:
            commission = min(commission, self.maximum)

        return commission


class SlippageEstimate(BaseModel):
    """Slippage estimation parameters."""

    basis_points: Decimal = Field(
        default=Decimal("5"),
        description="Expected slippage in basis points (default: 5 bps = 0.05%)",
    )
    fixed_amount: Decimal = Field(
        default=Decimal("0"),
        description="Fixed slippage per share (alternative to bps)",
    )

    def calculate(self, quantity: int, price: Decimal) -> Decimal:
        """Calculate estimated slippage cost for an order.

        Args:
            quantity: Number of shares/contracts
            price: Price per unit

        Returns:
            Estimated slippage cost
        """
        if self.fixed_amount > 0:
            return self.fixed_amount * Decimal(abs(quantity))

        # Basis points slippage
        notional = Decimal(abs(quantity)) * price
        return notional * (self.basis_points / Decimal("10000"))


class FeeConfig(BaseModel):
    """Fee and slippage configuration for risk calculations."""

    stock_commission: CommissionProfile = Field(
        default_factory=lambda: CommissionProfile(
            per_share=Decimal("0.005"),
            minimum=Decimal("1.00"),
            maximum=Decimal("0"),  # No cap
        ),
        description="US stock commission (IBKR Tiered: $0.005/share, min $1)",
    )
    forex_commission: CommissionProfile = Field(
        default_factory=lambda: CommissionProfile(
            per_share=Decimal("0"),
            minimum=Decimal("0"),
            percentage=Decimal("0.00002"),  # 0.2 bps = $2 per $10K
        ),
        description="FX commission (IBKR: 0.2 bps of notional)",
    )
    option_commission: CommissionProfile = Field(
        default_factory=lambda: CommissionProfile(
            per_share=Decimal("0.65"),  # Per contract
            minimum=Decimal("1.00"),
            maximum=Decimal("0"),
        ),
        description="Option commission (IBKR: $0.65-$1.00 per contract)",
    )
    futures_commission: CommissionProfile = Field(
        default_factory=lambda: CommissionProfile(
            per_share=Decimal("0.85"),  # Per contract
            minimum=Decimal("0"),
            maximum=Decimal("0"),
        ),
        description="Futures commission (varies by exchange)",
    )
    stock_slippage: SlippageEstimate = Field(
        default_factory=lambda: SlippageEstimate(basis_points=Decimal("5")),
        description="Stock slippage estimate (default: 5 bps)",
    )
    forex_slippage: SlippageEstimate = Field(
        default_factory=lambda: SlippageEstimate(basis_points=Decimal("1")),
        description="FX slippage estimate (default: 1 bp, highly liquid)",
    )
    option_slippage: SlippageEstimate = Field(
        default_factory=lambda: SlippageEstimate(basis_points=Decimal("20")),
        description="Option slippage estimate (default: 20 bps, wider spreads)",
    )
    futures_slippage: SlippageEstimate = Field(
        default_factory=lambda: SlippageEstimate(basis_points=Decimal("5")),
        description="Futures slippage estimate (default: 5 bps)",
    )

    def estimate_costs(
        self,
        contract: SymbolContract,
        side: OrderSide,
        quantity: int,
        price: Decimal,
    ) -> tuple[Decimal, Decimal]:
        """Estimate total transaction costs for an order.

        Args:
            contract: Trading contract
            side: Order side (BUY/SELL)
            quantity: Order quantity
            price: Expected execution price

        Returns:
            Tuple of (commission, slippage) estimates
        """
        sec_type = contract.sec_type.upper()

        # Select profile based on security type
        if sec_type == "STK":
            commission = self.stock_commission.calculate(quantity, price)
            slippage = self.stock_slippage.calculate(quantity, price)
        elif sec_type == "CASH":
            commission = self.forex_commission.calculate(quantity, price)
            slippage = self.forex_slippage.calculate(quantity, price)
        elif sec_type == "OPT":
            commission = self.option_commission.calculate(quantity, price)
            slippage = self.option_slippage.calculate(quantity, price)
        elif sec_type == "FUT":
            commission = self.futures_commission.calculate(quantity, price)
            slippage = self.futures_slippage.calculate(quantity, price)
        else:
            # Unknown type - use conservative stock estimates
            commission = self.stock_commission.calculate(quantity, price)
            slippage = self.stock_slippage.calculate(quantity, price)

        return commission, slippage

    def total_cost(
        self,
        contract: SymbolContract,
        side: OrderSide,
        quantity: int,
        price: Decimal,
    ) -> Decimal:
        """Calculate total estimated transaction cost (commission + slippage).

        Args:
            contract: Trading contract
            side: Order side (BUY/SELL)
            quantity: Order quantity
            price: Expected execution price

        Returns:
            Total estimated cost
        """
        commission, slippage = self.estimate_costs(contract, side, quantity, price)
        return commission + slippage
