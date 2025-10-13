"""Tests for portfolio state and risk guard."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from ibkr_trader.events import OrderStatusEvent
from ibkr_trader.models import OrderSide, OrderStatus, Position, SymbolContract
from ibkr_trader.portfolio import PortfolioState, RiskGuard


@pytest.mark.asyncio
async def test_portfolio_updates_account_and_positions() -> None:
    state = PortfolioState(max_daily_loss=Decimal("1000"))
    await state.update_account(
        {
            "NetLiquidation": "100000",
            "TotalCashValue": "50000",
            "BuyingPower": "200000",
        }
    )

    positions = [
        Position(
            contract=SymbolContract(symbol="AAPL"),
            quantity=10,
            avg_cost=Decimal("150"),
            market_value=Decimal("1500"),
            unrealized_pnl=Decimal("100"),
        )
    ]

    await state.update_positions(positions)

    snapshot = state.snapshot
    assert snapshot.net_liquidation == Decimal("100000")
    assert snapshot.positions["AAPL"].quantity == 10


@pytest.mark.asyncio
async def test_risk_guard_validates_exposure() -> None:
    state = PortfolioState(max_daily_loss=Decimal("1000"))
    guard = RiskGuard(portfolio=state, max_exposure=Decimal("5000"))

    contract = SymbolContract(symbol="AAPL")
    await guard.validate_order(contract, OrderSide.BUY, quantity=10, price=Decimal("100"))

    with pytest.raises(RuntimeError):
        await guard.validate_order(contract, OrderSide.BUY, quantity=100, price=Decimal("100"))


@pytest.mark.asyncio
async def test_risk_guard_handles_fill_event() -> None:
    state = PortfolioState(max_daily_loss=Decimal("1000"))
    guard = RiskGuard(portfolio=state, max_exposure=Decimal("5000"))

    event = OrderStatusEvent(
        order_id=1,
        status=OrderStatus.FILLED,
        contract=SymbolContract(symbol="AAPL"),
        side=OrderSide.BUY,
        filled=5,
        remaining=0,
        avg_fill_price=120.0,
        timestamp=datetime.now(tz=UTC),
    )
    await guard.handle_order_status(event)


@pytest.mark.asyncio
async def test_portfolio_persist_writes_snapshot(tmp_path: Path) -> None:
    snapshot_path = tmp_path / "portfolio.json"
    state = PortfolioState(
        max_daily_loss=Decimal("1000"),
        snapshot_path=snapshot_path,
    )

    await state.update_account(
        {
            "NetLiquidation": "50000",
            "TotalCashValue": "25000",
            "BuyingPower": "75000",
        }
    )
    await state.persist()

    assert snapshot_path.exists()

    restored = PortfolioState(
        max_daily_loss=Decimal("1000"),
        snapshot_path=snapshot_path,
    )
    assert restored.snapshot.net_liquidation == Decimal("50000")
