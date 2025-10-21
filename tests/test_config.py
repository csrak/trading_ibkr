"""Tests for IBKRConfig fee configuration."""

from decimal import Decimal

import pytest

from ibkr_trader.config import IBKRConfig


def test_config_default_fee_settings() -> None:
    """Test that config has sensible default fee settings."""
    config = IBKRConfig()

    # Fee estimates disabled by default
    assert config.enable_fee_estimates is False

    # Stock commission defaults
    assert config.stock_commission_per_share == 0.005
    assert config.stock_commission_minimum == 1.00
    assert config.stock_slippage_bps == 5.0

    # FX commission defaults
    assert config.forex_commission_percentage == 0.00002
    assert config.forex_slippage_bps == 1.0

    # Option commission defaults
    assert config.option_commission_per_contract == 0.65
    assert config.option_slippage_bps == 20.0


def test_config_create_fee_config() -> None:
    """Test creating FeeConfig from IBKRConfig."""
    config = IBKRConfig()
    fee_config = config.create_fee_config()

    # Verify stock commission profile
    assert fee_config.stock_commission.per_share == Decimal("0.005")
    assert fee_config.stock_commission.minimum == Decimal("1.00")

    # Verify stock slippage
    assert fee_config.stock_slippage.basis_points == Decimal("5.0")

    # Verify FX commission profile
    assert fee_config.forex_commission.percentage == Decimal("0.00002")

    # Verify FX slippage
    assert fee_config.forex_slippage.basis_points == Decimal("1.0")

    # Verify option commission profile
    assert fee_config.option_commission.per_share == Decimal("0.65")

    # Verify option slippage
    assert fee_config.option_slippage.basis_points == Decimal("20.0")


def test_config_custom_fee_settings() -> None:
    """Test custom fee settings."""
    config = IBKRConfig(
        enable_fee_estimates=True,
        stock_commission_per_share=0.01,
        stock_commission_minimum=2.00,
        stock_slippage_bps=10.0,
    )

    assert config.enable_fee_estimates is True
    assert config.stock_commission_per_share == 0.01
    assert config.stock_commission_minimum == 2.00
    assert config.stock_slippage_bps == 10.0

    # Create fee config with custom settings
    fee_config = config.create_fee_config()
    assert fee_config.stock_commission.per_share == Decimal("0.01")
    assert fee_config.stock_commission.minimum == Decimal("2.00")
    assert fee_config.stock_slippage.basis_points == Decimal("10.0")


def test_fee_config_calculations_from_ibkr_config() -> None:
    """Test that FeeConfig created from IBKRConfig calculates correctly."""
    config = IBKRConfig()
    fee_config = config.create_fee_config()

    from ibkr_trader.models import OrderSide, SymbolContract

    # Test stock commission calculation
    contract = SymbolContract(symbol="AAPL", sec_type="STK")
    commission, slippage = fee_config.estimate_costs(
        contract=contract,
        side=OrderSide.BUY,
        quantity=100,
        price=Decimal("50"),
    )

    # Commission: 100 * 0.005 = 0.50, min 1.00 -> 1.00
    assert commission == Decimal("1.00")

    # Slippage: 5000 * 0.0005 = 2.50
    assert slippage == Decimal("2.50")


def test_config_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that fee settings can be configured via environment variables."""
    monkeypatch.setenv("IBKR_ENABLE_FEE_ESTIMATES", "true")
    monkeypatch.setenv("IBKR_STOCK_COMMISSION_PER_SHARE", "0.003")
    monkeypatch.setenv("IBKR_STOCK_SLIPPAGE_BPS", "3.0")

    config = IBKRConfig()

    assert config.enable_fee_estimates is True
    assert config.stock_commission_per_share == 0.003
    assert config.stock_slippage_bps == 3.0


def test_fee_config_uses_config_values() -> None:
    """Test that FeeConfig properly uses values from IBKRConfig."""
    config = IBKRConfig(
        forex_commission_percentage=0.00005,  # Higher than default
        forex_slippage_bps=2.0,  # Higher than default
    )

    fee_config = config.create_fee_config()

    # Should reflect custom values
    assert fee_config.forex_commission.percentage == Decimal("0.00005")
    assert fee_config.forex_slippage.basis_points == Decimal("2.0")
