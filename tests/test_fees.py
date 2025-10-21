"""Tests for fee and slippage estimation."""

from decimal import Decimal

from ibkr_trader.models import OrderSide, SymbolContract
from ibkr_trader.risk.fees import CommissionProfile, FeeConfig, SlippageEstimate


def test_commission_profile_per_share() -> None:
    """Test per-share commission calculation."""
    profile = CommissionProfile(
        per_share=Decimal("0.005"),
        minimum=Decimal("1.00"),
        maximum=Decimal("0"),
    )

    # Small order: hit minimum
    commission = profile.calculate(quantity=100, price=Decimal("50"))
    assert commission == Decimal("1.00")  # 100 * 0.005 = 0.50, but min is 1.00

    # Large order: per-share applies
    commission = profile.calculate(quantity=1000, price=Decimal("50"))
    assert commission == Decimal("5.00")  # 1000 * 0.005 = 5.00


def test_commission_profile_percentage() -> None:
    """Test percentage-based commission calculation."""
    profile = CommissionProfile(
        percentage=Decimal("0.0002"),  # 2 bps
        minimum=Decimal("2.00"),
    )

    # Notional = 10,000 * 1.10 = 11,000
    # Commission = 11,000 * 0.0002 = 2.20
    commission = profile.calculate(quantity=10000, price=Decimal("1.10"))
    assert commission == Decimal("2.20")


def test_commission_profile_maximum_cap() -> None:
    """Test maximum commission cap."""
    profile = CommissionProfile(
        per_share=Decimal("0.01"),
        minimum=Decimal("0"),
        maximum=Decimal("10.00"),  # Cap at $10
    )

    # Would be $20 (2000 * 0.01), capped at $10
    commission = profile.calculate(quantity=2000, price=Decimal("100"))
    assert commission == Decimal("10.00")


def test_slippage_estimate_basis_points() -> None:
    """Test slippage calculation using basis points."""
    slippage = SlippageEstimate(basis_points=Decimal("5"))  # 5 bps = 0.05%

    # Notional = 100 * 50 = 5000
    # Slippage = 5000 * 0.0005 = 2.50
    cost = slippage.calculate(quantity=100, price=Decimal("50"))
    assert cost == Decimal("2.50")


def test_slippage_estimate_fixed_amount() -> None:
    """Test slippage calculation using fixed amount per share."""
    slippage = SlippageEstimate(
        fixed_amount=Decimal("0.01"),
        basis_points=Decimal("0"),  # Ignored when fixed_amount is set
    )

    # 100 shares * $0.01 = $1.00
    cost = slippage.calculate(quantity=100, price=Decimal("50"))
    assert cost == Decimal("1.00")


def test_fee_config_stock_costs() -> None:
    """Test fee calculation for stocks."""
    config = FeeConfig()
    contract = SymbolContract(symbol="AAPL", sec_type="STK")

    commission, slippage = config.estimate_costs(
        contract=contract,
        side=OrderSide.BUY,
        quantity=100,
        price=Decimal("150"),
    )

    # Stock commission: 100 * 0.005 = 0.50, min 1.00 -> 1.00
    assert commission == Decimal("1.00")

    # Stock slippage: 15000 * 0.0005 = 7.50
    assert slippage == Decimal("7.50")


def test_fee_config_forex_costs() -> None:
    """Test fee calculation for forex."""
    config = FeeConfig()
    contract = SymbolContract(symbol="EUR", sec_type="CASH", exchange="IDEALPRO", currency="USD")

    commission, slippage = config.estimate_costs(
        contract=contract,
        side=OrderSide.BUY,
        quantity=10000,
        price=Decimal("1.10"),
    )

    # FX commission: 11000 * 0.00002 = 0.22
    assert commission == Decimal("0.22")

    # FX slippage: 11000 * 0.0001 = 1.10 (1 bp)
    assert slippage == Decimal("1.10")


def test_fee_config_option_costs() -> None:
    """Test fee calculation for options."""
    config = FeeConfig()
    contract = SymbolContract(symbol="AAPL", sec_type="OPT")

    commission, slippage = config.estimate_costs(
        contract=contract,
        side=OrderSide.BUY,
        quantity=10,  # 10 contracts
        price=Decimal("5.00"),
    )

    # Option commission: 10 * 0.65 = 6.50
    assert commission == Decimal("6.50")

    # Option slippage: 50 * 0.0020 = 0.10 (20 bps)
    assert slippage == Decimal("0.10")


def test_fee_config_total_cost() -> None:
    """Test total cost calculation (commission + slippage)."""
    config = FeeConfig()
    contract = SymbolContract(symbol="AAPL", sec_type="STK")

    total = config.total_cost(
        contract=contract,
        side=OrderSide.BUY,
        quantity=100,
        price=Decimal("150"),
    )

    # Commission: 1.00, Slippage: 7.50 -> Total: 8.50
    assert total == Decimal("8.50")


def test_fee_config_unknown_sec_type() -> None:
    """Test that unknown security types fall back to stock estimates."""
    config = FeeConfig()
    contract = SymbolContract(symbol="UNKNOWN", sec_type="UNKNOWN")

    commission, slippage = config.estimate_costs(
        contract=contract,
        side=OrderSide.BUY,
        quantity=100,
        price=Decimal("100"),
    )

    # Should use stock defaults
    # Commission: 100 * 0.005 = 0.50, min 1.00 -> 1.00
    assert commission == Decimal("1.00")

    # Slippage: 10000 * 0.0005 = 5.00
    assert slippage == Decimal("5.00")


def test_commission_profile_negative_quantity() -> None:
    """Test that negative quantities are handled correctly."""
    profile = CommissionProfile(per_share=Decimal("0.005"), minimum=Decimal("1.00"))

    # abs(-200) = 200 shares
    commission = profile.calculate(quantity=-200, price=Decimal("50"))
    assert commission == Decimal("1.00")  # 200 * 0.005 = 1.00


def test_slippage_estimate_negative_quantity() -> None:
    """Test that negative quantities are handled correctly in slippage."""
    slippage = SlippageEstimate(basis_points=Decimal("5"))

    # abs(-100) = 100 shares
    cost = slippage.calculate(quantity=-100, price=Decimal("50"))
    assert cost == Decimal("2.50")  # 5000 * 0.0005 = 2.50


def test_fee_config_custom_profiles() -> None:
    """Test custom commission and slippage profiles."""
    config = FeeConfig(
        stock_commission=CommissionProfile(
            per_share=Decimal("0.01"),  # Higher than default
            minimum=Decimal("5.00"),
            maximum=Decimal("50.00"),
        ),
        stock_slippage=SlippageEstimate(
            basis_points=Decimal("10")  # Higher than default
        ),
    )

    contract = SymbolContract(symbol="AAPL", sec_type="STK")

    commission, slippage = config.estimate_costs(
        contract=contract,
        side=OrderSide.BUY,
        quantity=100,
        price=Decimal("150"),
    )

    # Custom commission: 100 * 0.01 = 1.00, min 5.00 -> 5.00
    assert commission == Decimal("5.00")

    # Custom slippage: 15000 * 0.0010 = 15.00
    assert slippage == Decimal("15.00")
