"""Tests for per-symbol risk limits and registry persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from ibkr_trader.events import ExecutionEvent
from ibkr_trader.models import OrderSide, SymbolContract
from ibkr_trader.portfolio import PortfolioState, RiskGuard, SymbolLimitRegistry


@pytest.mark.asyncio
async def test_symbol_limits_override_global() -> None:
    """Symbol-specific position limits should override global limits."""

    portfolio = PortfolioState(max_daily_loss=Decimal("1000"))
    registry = SymbolLimitRegistry()
    registry.set_symbol_limit(
        symbol="AAPL",
        max_position_size=5,
        max_order_exposure=Decimal("5000"),
        max_daily_loss=Decimal("500"),
    )
    guard = RiskGuard(
        portfolio=portfolio,
        max_exposure=Decimal("10000"),
        symbol_limits=registry,
    )

    # Order within limit should pass
    await guard.validate_order(
        contract=SymbolContract(symbol="AAPL"),
        side=OrderSide.BUY,
        quantity=5,
        price=Decimal("100"),
    )

    # Order exceeding limit should raise
    with pytest.raises(RuntimeError) as exc_info:
        await guard.validate_order(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.BUY,
            quantity=6,
            price=Decimal("100"),
        )

    assert "exceeds per-symbol limit" in str(exc_info.value)


@pytest.mark.asyncio
async def test_symbol_limits_fallback_to_default() -> None:
    """Default limits apply when no symbol-specific override exists."""

    portfolio = PortfolioState(max_daily_loss=Decimal("1000"))
    registry = SymbolLimitRegistry()
    registry.set_default_limit(
        max_position_size=10,
        max_order_exposure=Decimal("6000"),
        max_daily_loss=Decimal("400"),
    )
    guard = RiskGuard(
        portfolio=portfolio,
        max_exposure=Decimal("20000"),
        symbol_limits=registry,
    )

    # Within default limit
    await guard.validate_order(
        contract=SymbolContract(symbol="MSFT"),
        side=OrderSide.BUY,
        quantity=10,
        price=Decimal("100"),
    )

    # Exceeds default limit
    with pytest.raises(RuntimeError):
        await guard.validate_order(
            contract=SymbolContract(symbol="MSFT"),
            side=OrderSide.BUY,
            quantity=11,
            price=Decimal("100"),
        )


@pytest.mark.asyncio
async def test_symbol_limits_daily_loss_enforced() -> None:
    """Daily loss limit for a symbol blocks additional orders."""

    portfolio = PortfolioState(max_daily_loss=Decimal("1000"))
    registry = SymbolLimitRegistry()
    registry.set_symbol_limit(
        symbol="TSLA",
        max_position_size=50,
        max_order_exposure=Decimal("10000"),
        max_daily_loss=Decimal("150"),
    )
    guard = RiskGuard(
        portfolio=portfolio,
        max_exposure=Decimal("50000"),
        symbol_limits=registry,
    )

    # Record a losing execution to breach per-symbol daily loss limit
    await portfolio.record_execution_event(
        ExecutionEvent(
            order_id=1,
            contract=SymbolContract(symbol="TSLA"),
            side=OrderSide.BUY,
            quantity=1,
            price=Decimal("200"),
            commission=Decimal("0"),
            timestamp=datetime.now(UTC),
        )
    )

    with pytest.raises(RuntimeError) as exc_info:
        await guard.validate_order(
            contract=SymbolContract(symbol="TSLA"),
            side=OrderSide.BUY,
            quantity=1,
            price=Decimal("200"),
        )

    assert "Daily loss limit reached" in str(exc_info.value)


def test_symbol_limits_persistence(tmp_path: Path) -> None:
    """Symbol limit configuration persists to disk and can be reloaded."""

    registry = SymbolLimitRegistry()
    registry.set_default_limit(
        max_position_size=25,
        max_order_exposure=Decimal("7500"),
        max_daily_loss=Decimal("300"),
    )
    registry.set_symbol_limit(
        symbol="AAPL",
        max_position_size=5,
        max_order_exposure=Decimal("2500"),
        max_daily_loss=Decimal("150"),
    )

    path = tmp_path / "symbol_limits.json"
    registry.save_config(path)

    reloaded = SymbolLimitRegistry(config_path=path)
    default_limits = reloaded.default_limits
    symbol_limits = reloaded.get_limit("AAPL")

    assert default_limits is not None
    assert default_limits.max_position_size == 25
    assert default_limits.max_order_exposure == Decimal("7500")
    assert default_limits.max_daily_loss == Decimal("300")

    assert symbol_limits is not None
    assert symbol_limits.max_position_size == 5
    assert symbol_limits.max_order_exposure == Decimal("2500")
    assert symbol_limits.max_daily_loss == Decimal("150")
