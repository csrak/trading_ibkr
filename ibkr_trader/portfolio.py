"""Portfolio state tracking and risk checks."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from loguru import logger

from ibkr_trader.events import ExecutionEvent, OrderStatusEvent
from ibkr_trader.models import OrderSide, OrderStatus, Position, SymbolContract


@dataclass(slots=True)
class PortfolioSnapshot:
    """In-memory snapshot of positions and account metrics."""

    positions: dict[str, Position] = field(default_factory=dict)
    net_liquidation: Decimal = Decimal("0")
    total_cash: Decimal = Decimal("0")
    buying_power: Decimal = Decimal("0")
    realized_pnl_today: Decimal = Decimal("0")


class PortfolioState:
    """Tracks portfolio state and evaluates risk limits."""

    def __init__(
        self,
        max_daily_loss: Decimal,
        snapshot_path: Path | None = None,
    ) -> None:
        self.snapshot = PortfolioSnapshot()
        self._max_daily_loss = max_daily_loss
        self._loss_check_date: date | None = None
        self._lock = asyncio.Lock()
        self._snapshot_path = snapshot_path
        if snapshot_path is not None:
            self._load_snapshot(snapshot_path)

        self._trade_stats: dict[str, Decimal] = {
            "fills": Decimal("0"),
            "buy_volume": Decimal("0"),
            "sell_volume": Decimal("0"),
        }
        self._realized_pnl = Decimal("0")
        self._symbol_pnl: dict[str, Decimal] = {}

    async def update_account(self, summary: dict[str, str]) -> None:
        async with self._lock:
            self.snapshot.net_liquidation = Decimal(summary.get("NetLiquidation", "0"))
            self.snapshot.total_cash = Decimal(summary.get("TotalCashValue", "0"))
            self.snapshot.buying_power = Decimal(summary.get("BuyingPower", "0"))

    async def update_positions(self, positions: list[Position]) -> None:
        async with self._lock:
            self.snapshot.positions = {pos.contract.symbol: pos for pos in positions}

    async def record_order_fill(
        self, symbol: str, side: OrderSide, filled: int, avg_price: Decimal
    ) -> None:
        # placeholder for realized pnl tracking; to be expanded with executions
        logger.debug(
            "Order fill recorded: symbol=%s side=%s filled=%s avg_price=%s",
            symbol,
            side,
            filled,
            avg_price,
        )

    async def record_execution_event(self, event: ExecutionEvent) -> None:
        await self.record_order_fill(
            symbol=event.contract.symbol,
            side=event.side,
            filled=event.quantity,
            avg_price=event.price,
        )

        volume = Decimal(event.quantity) * event.price
        async with self._lock:
            self._trade_stats["fills"] += Decimal("1")
            if event.side == OrderSide.BUY:
                self._trade_stats["buy_volume"] += volume
            else:
                self._trade_stats["sell_volume"] += volume
            side_multiplier = Decimal("1") if event.side == OrderSide.SELL else Decimal("-1")
            pnl_delta = side_multiplier * volume
            self._realized_pnl += pnl_delta
            self._symbol_pnl[event.contract.symbol] = (
                self._symbol_pnl.get(event.contract.symbol, Decimal("0")) + pnl_delta
            )

    async def check_daily_loss_limit(self) -> None:
        async with self._lock:
            today = datetime.now(UTC).date()
            if self._loss_check_date != today:
                self._loss_check_date = today
                self.snapshot.realized_pnl_today = Decimal("0")

            if self.snapshot.realized_pnl_today <= -self._max_daily_loss:
                message = (
                    "Daily loss limit reached: "
                    f"{self.snapshot.realized_pnl_today} <= -{self._max_daily_loss}"
                )
                raise RuntimeError(message)

    def _load_snapshot(self, path: Path) -> None:
        if not path.exists():
            return
        try:
            data = path.read_text(encoding="utf-8")
            decoded = json.loads(data)
            positions = {
                symbol: Position(**payload)
                for symbol, payload in decoded.get("positions", {}).items()
            }
            self.snapshot = PortfolioSnapshot(
                positions=positions,
                net_liquidation=Decimal(decoded.get("net_liquidation", "0")),
                total_cash=Decimal(decoded.get("total_cash", "0")),
                buying_power=Decimal(decoded.get("buying_power", "0")),
                realized_pnl_today=Decimal(decoded.get("realized_pnl_today", "0")),
            )
            trade_stats = decoded.get("trade_stats") or {}
            self._trade_stats = {
                "fills": Decimal(trade_stats.get("fills", "0")),
                "buy_volume": Decimal(trade_stats.get("buy_volume", "0")),
                "sell_volume": Decimal(trade_stats.get("sell_volume", "0")),
            }
            self._realized_pnl = Decimal(decoded.get("realized_pnl", "0"))
            symbol_pnl = decoded.get("symbol_pnl") or {}
            self._symbol_pnl = {symbol: Decimal(value) for symbol, value in symbol_pnl.items()}
            logger.info("Loaded portfolio snapshot from %s", path)
        except Exception as exc:  # pragma: no cover - only on IO failure
            logger.warning("Failed to load portfolio snapshot: %s", exc)

    async def persist(self) -> None:
        if self._snapshot_path is None:
            return
        async with self._lock:
            data: dict[str, Any] = {
                "net_liquidation": str(self.snapshot.net_liquidation),
                "total_cash": str(self.snapshot.total_cash),
                "buying_power": str(self.snapshot.buying_power),
                "realized_pnl_today": str(self.snapshot.realized_pnl_today),
                "trade_stats": {key: str(value) for key, value in self._trade_stats.items()},
                "realized_pnl": str(self._realized_pnl),
                "symbol_pnl": {symbol: str(value) for symbol, value in self._symbol_pnl.items()},
                "positions": {
                    symbol: position.model_dump()
                    for symbol, position in self.snapshot.positions.items()
                },
            }
        try:
            self._snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            self._snapshot_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:  # pragma: no cover - only on IO failure
            logger.warning("Failed to persist portfolio snapshot: %s", exc)

    async def trade_statistics(self) -> dict[str, str]:
        async with self._lock:
            return {key: str(value) for key, value in self._trade_stats.items()}

    async def realized_pnl(self) -> str:
        async with self._lock:
            return str(self._realized_pnl)

    async def per_symbol_pnl(self) -> dict[str, str]:
        async with self._lock:
            return {symbol: str(value) for symbol, value in self._symbol_pnl.items()}


class RiskGuard:
    """Additional risk validations for advanced strategies."""

    def __init__(self, portfolio: PortfolioState, max_exposure: Decimal) -> None:
        self.portfolio = portfolio
        self.max_exposure = max_exposure

    async def validate_order(
        self, contract: SymbolContract, side: OrderSide, quantity: int, price: Decimal
    ) -> None:
        await self.portfolio.check_daily_loss_limit()

        exposure = quantity * price
        if price <= 0:
            logger.debug(
                "Skipping exposure check for %s due to non-positive price %s",
                contract.symbol,
                price,
            )
            return

        if exposure > self.max_exposure:
            message = (
                f"Order exposure {exposure} exceeds max exposure "
                f"{self.max_exposure} for {contract.symbol}"
            )
            raise RuntimeError(message)

    async def handle_order_status(self, event: OrderStatusEvent) -> None:
        if event.status != OrderStatus.FILLED or event.filled <= 0:
            return

        await self.portfolio.record_order_fill(
            symbol=event.contract.symbol,
            side=event.side,
            filled=event.filled,
            avg_price=Decimal(str(event.avg_fill_price)),
        )
