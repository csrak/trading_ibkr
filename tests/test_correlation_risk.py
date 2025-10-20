"""Tests for correlation-based risk guard and matrix utilities."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from ibkr_trader.models import OrderSide, Position, SymbolContract
from ibkr_trader.portfolio import PortfolioState
from ibkr_trader.risk import CorrelationMatrix, CorrelationRiskGuard


@pytest.mark.asyncio
async def test_correlation_guard_blocks_excessive_exposure() -> None:
    portfolio = PortfolioState(max_daily_loss=Decimal("1000"))
    msft_position = Position(
        contract=SymbolContract(symbol="MSFT"),
        quantity=10,
        avg_cost=Decimal("100"),
        market_value=Decimal("1000"),
        unrealized_pnl=Decimal("0"),
    )
    await portfolio.update_positions([msft_position])

    matrix = CorrelationMatrix()
    matrix.set_correlation("AAPL", "MSFT", 0.9)
    guard = CorrelationRiskGuard(
        correlation_matrix=matrix,
        max_correlated_exposure=Decimal("1500"),
        threshold=0.8,
    )

    with pytest.raises(RuntimeError, match="Correlated exposure limit exceeded"):
        await guard.validate_order(
            contract=SymbolContract(symbol="AAPL"),
            side=OrderSide.BUY,
            quantity=10,
            price=Decimal("100"),
            portfolio=portfolio,
        )


@pytest.mark.asyncio
async def test_correlation_guard_allows_exposure_reduction() -> None:
    portfolio = PortfolioState(max_daily_loss=Decimal("1000"))
    aapl_position = Position(
        contract=SymbolContract(symbol="AAPL"),
        quantity=10,
        avg_cost=Decimal("100"),
        market_value=Decimal("1000"),
        unrealized_pnl=Decimal("0"),
    )
    msft_position = Position(
        contract=SymbolContract(symbol="MSFT"),
        quantity=10,
        avg_cost=Decimal("100"),
        market_value=Decimal("1000"),
        unrealized_pnl=Decimal("0"),
    )
    await portfolio.update_positions([aapl_position, msft_position])

    matrix = CorrelationMatrix()
    matrix.set_correlation("AAPL", "MSFT", 0.9)
    guard = CorrelationRiskGuard(
        correlation_matrix=matrix,
        max_correlated_exposure=Decimal("1500"),
        threshold=0.8,
    )

    # Selling the entire AAPL position should reduce exposure and be allowed.
    await guard.validate_order(
        contract=SymbolContract(symbol="AAPL"),
        side=OrderSide.SELL,
        quantity=10,
        price=Decimal("100"),
        portfolio=portfolio,
    )


@pytest.mark.asyncio
async def test_correlation_guard_ignores_unrelated_symbol() -> None:
    portfolio = PortfolioState(max_daily_loss=Decimal("1000"))
    matrix = CorrelationMatrix()
    matrix.set_correlation("AAPL", "MSFT", 0.6)  # below default threshold of 0.75
    guard = CorrelationRiskGuard(
        correlation_matrix=matrix,
        max_correlated_exposure=Decimal("100"),
        threshold=0.75,
    )

    # No correlated symbols above threshold -> no exception
    await guard.validate_order(
        contract=SymbolContract(symbol="AAPL"),
        side=OrderSide.BUY,
        quantity=1,
        price=Decimal("100"),
        portfolio=portfolio,
    )


def test_correlation_matrix_persistence(tmp_path: Path) -> None:
    matrix = CorrelationMatrix()
    matrix.set_correlation("AAPL", "MSFT", 0.85)
    matrix.set_correlation("AAPL", "GOOGL", 0.4)

    path = tmp_path / "correlation.json"
    matrix.save(path)

    reloaded = CorrelationMatrix.load(path)
    assert reloaded is not None
    assert pytest.approx(reloaded.get_correlation("MSFT", "AAPL"), rel=1e-6) == 0.85
    assert reloaded.get_correlated_symbols("AAPL", 0.8) == ["MSFT"]
