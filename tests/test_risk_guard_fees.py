"""Tests for fee-aware RiskGuard validation."""

from decimal import Decimal
from pathlib import Path

import pytest

from ibkr_trader.models import OrderSide, SymbolContract
from ibkr_trader.risk import FeeConfig, PortfolioState, RiskGuard
from ibkr_trader.risk.fees import CommissionProfile, SlippageEstimate


@pytest.fixture
def portfolio_state(tmp_path: Path) -> PortfolioState:
    """Create a portfolio state for testing."""
    return PortfolioState(
        max_daily_loss=Decimal("1000"),
        snapshot_path=tmp_path / "portfolio.json",
    )


@pytest.fixture
def fee_config() -> FeeConfig:
    """Create a fee configuration for testing."""
    return FeeConfig(
        stock_commission=CommissionProfile(
            per_share=Decimal("0.005"),
            minimum=Decimal("1.00"),
        ),
        stock_slippage=SlippageEstimate(
            basis_points=Decimal("5")  # 5 bps
        ),
    )


@pytest.mark.asyncio
async def test_risk_guard_without_fees(portfolio_state: PortfolioState) -> None:
    """Test RiskGuard without fee configuration (backward compatible)."""
    guard = RiskGuard(
        portfolio=portfolio_state,
        max_exposure=Decimal("10000"),
        fee_config=None,  # No fees
    )

    contract = SymbolContract(symbol="AAPL", sec_type="STK")

    # Order exposure: 100 * 50 = 5000 (within limit)
    await guard.validate_order(
        contract=contract,
        side=OrderSide.BUY,
        quantity=100,
        price=Decimal("50"),
    )


@pytest.mark.asyncio
async def test_risk_guard_with_fees_within_limit(
    portfolio_state: PortfolioState, fee_config: FeeConfig
) -> None:
    """Test RiskGuard with fees - order within exposure limit."""
    guard = RiskGuard(
        portfolio=portfolio_state,
        max_exposure=Decimal("10000"),
        fee_config=fee_config,
    )

    contract = SymbolContract(symbol="AAPL", sec_type="STK")

    # Base exposure: 100 * 50 = 5000
    # Commission: 1.00 (minimum)
    # Slippage: 5000 * 0.0005 = 2.50
    # Total: 5003.50 (within 10000 limit)
    await guard.validate_order(
        contract=contract,
        side=OrderSide.BUY,
        quantity=100,
        price=Decimal("50"),
    )


@pytest.mark.asyncio
async def test_risk_guard_with_fees_exceeds_limit(
    portfolio_state: PortfolioState, fee_config: FeeConfig
) -> None:
    """Test RiskGuard with fees - order exceeds exposure limit due to fees."""
    guard = RiskGuard(
        portfolio=portfolio_state,
        max_exposure=Decimal("5000"),  # Tight limit
        fee_config=fee_config,
    )

    contract = SymbolContract(symbol="AAPL", sec_type="STK")

    # Base exposure: 100 * 50 = 5000
    # Commission: 1.00
    # Slippage: 2.50
    # Total: 5003.50 (exceeds 5000 limit)
    with pytest.raises(RuntimeError, match="Order exposure.*exceeds max exposure"):
        await guard.validate_order(
            contract=contract,
            side=OrderSide.BUY,
            quantity=100,
            price=Decimal("50"),
        )


@pytest.mark.asyncio
async def test_risk_guard_large_order_with_fees(
    portfolio_state: PortfolioState, fee_config: FeeConfig
) -> None:
    """Test RiskGuard with significant fees on large order."""
    guard = RiskGuard(
        portfolio=portfolio_state,
        max_exposure=Decimal("100000"),
        fee_config=fee_config,
    )

    contract = SymbolContract(symbol="AAPL", sec_type="STK")

    # Base exposure: 1000 * 75 = 75000
    # Commission: 1000 * 0.005 = 5.00
    # Slippage: 75000 * 0.0005 = 37.50
    # Total: 75042.50 (within 100000 limit)
    await guard.validate_order(
        contract=contract,
        side=OrderSide.BUY,
        quantity=1000,
        price=Decimal("75"),
    )


@pytest.mark.asyncio
async def test_risk_guard_forex_with_fees(portfolio_state: PortfolioState) -> None:
    """Test RiskGuard with FX-specific fees."""
    fx_config = FeeConfig(
        forex_commission=CommissionProfile(percentage=Decimal("0.00002")),  # 0.2 bps
        forex_slippage=SlippageEstimate(basis_points=Decimal("1")),  # 1 bp
    )

    guard = RiskGuard(
        portfolio=portfolio_state,
        max_exposure=Decimal("15000"),
        fee_config=fx_config,
    )

    contract = SymbolContract(
        symbol="EUR",
        sec_type="CASH",
        exchange="IDEALPRO",
        currency="USD",
    )

    # Base exposure: 10000 * 1.10 = 11000
    # Commission: 11000 * 0.00002 = 0.22
    # Slippage: 11000 * 0.0001 = 1.10
    # Total: 11001.32 (within 15000 limit)
    await guard.validate_order(
        contract=contract,
        side=OrderSide.BUY,
        quantity=10000,
        price=Decimal("1.10"),
    )


@pytest.mark.asyncio
async def test_risk_guard_high_slippage_config(portfolio_state: PortfolioState) -> None:
    """Test RiskGuard with high slippage estimate."""
    high_slippage_config = FeeConfig(
        stock_commission=CommissionProfile(per_share=Decimal("0.005"), minimum=Decimal("1.00")),
        stock_slippage=SlippageEstimate(basis_points=Decimal("50")),  # 50 bps = 0.5%
    )

    guard = RiskGuard(
        portfolio=portfolio_state,
        max_exposure=Decimal("10000"),
        fee_config=high_slippage_config,
    )

    contract = SymbolContract(symbol="ILLIQUID", sec_type="STK")

    # Base exposure: 100 * 50 = 5000
    # Commission: 1.00
    # Slippage: 5000 * 0.0050 = 25.00  # High slippage!
    # Total: 5026.00 (within 10000 limit)
    await guard.validate_order(
        contract=contract,
        side=OrderSide.BUY,
        quantity=100,
        price=Decimal("50"),
    )


@pytest.mark.asyncio
async def test_risk_guard_fees_with_zero_price(
    portfolio_state: PortfolioState, fee_config: FeeConfig
) -> None:
    """Test RiskGuard skips exposure check for zero price even with fees."""
    guard = RiskGuard(
        portfolio=portfolio_state,
        max_exposure=Decimal("1000"),  # Very low limit
        fee_config=fee_config,
    )

    contract = SymbolContract(symbol="AAPL", sec_type="STK")

    # Should not raise despite low limit (price is 0)
    await guard.validate_order(
        contract=contract,
        side=OrderSide.BUY,
        quantity=10000,
        price=Decimal("0"),
    )


@pytest.mark.asyncio
async def test_risk_guard_sell_order_with_fees(
    portfolio_state: PortfolioState, fee_config: FeeConfig
) -> None:
    """Test RiskGuard with fees on sell order."""
    guard = RiskGuard(
        portfolio=portfolio_state,
        max_exposure=Decimal("10000"),
        fee_config=fee_config,
    )

    contract = SymbolContract(symbol="AAPL", sec_type="STK")

    # Base exposure: 100 * 50 = 5000
    # Fees apply to both buy and sell
    await guard.validate_order(
        contract=contract,
        side=OrderSide.SELL,
        quantity=100,
        price=Decimal("50"),
    )


@pytest.mark.asyncio
async def test_risk_guard_option_fees(portfolio_state: PortfolioState) -> None:
    """Test RiskGuard with option-specific fees."""
    option_config = FeeConfig(
        option_commission=CommissionProfile(per_share=Decimal("0.65"), minimum=Decimal("1.00")),
        option_slippage=SlippageEstimate(basis_points=Decimal("20")),  # 20 bps
    )

    guard = RiskGuard(
        portfolio=portfolio_state,
        max_exposure=Decimal("1000"),
        fee_config=option_config,
    )

    contract = SymbolContract(symbol="AAPL", sec_type="OPT")

    # Base exposure: 10 * 5.00 = 50
    # Commission: 10 * 0.65 = 6.50
    # Slippage: 50 * 0.0020 = 0.10
    # Total: 56.60 (within 1000 limit)
    await guard.validate_order(
        contract=contract,
        side=OrderSide.BUY,
        quantity=10,
        price=Decimal("5.00"),
    )
