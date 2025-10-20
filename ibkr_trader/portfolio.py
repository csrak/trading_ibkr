"""Portfolio state tracking and risk checks."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from ibkr_trader.events import ExecutionEvent, OrderStatusEvent
from ibkr_trader.models import OrderSide, OrderStatus, Position, SymbolContract

if TYPE_CHECKING:
    from ibkr_trader.risk import CorrelationRiskGuard


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
        self._symbol_daily_pnl: dict[str, Decimal] = {}

    def _find_position_locked(self, symbol: str) -> Position | None:
        """Locate position using case-insensitive symbol matching.

        NOTE: Caller must hold self._lock.
        """
        for candidate in (symbol, symbol.upper(), symbol.lower()):
            position = self.snapshot.positions.get(candidate)
            if position is not None:
                return position
        return None

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
            self._ensure_daily_rollover(event.timestamp.date())

            self._trade_stats["fills"] += Decimal("1")
            if event.side == OrderSide.BUY:
                self._trade_stats["buy_volume"] += volume
            else:
                self._trade_stats["sell_volume"] += volume
            side_multiplier = Decimal("1") if event.side == OrderSide.SELL else Decimal("-1")
            pnl_delta = side_multiplier * volume
            self._realized_pnl += pnl_delta
            symbol = event.contract.symbol.upper()
            self._symbol_pnl[symbol] = self._symbol_pnl.get(symbol, Decimal("0")) + pnl_delta
            self._symbol_daily_pnl[symbol] = (
                self._symbol_daily_pnl.get(symbol, Decimal("0")) + pnl_delta
            )
            self.snapshot.realized_pnl_today += pnl_delta

    async def check_daily_loss_limit(self) -> None:
        async with self._lock:
            today = datetime.now(UTC).date()
            self._ensure_daily_rollover(today)

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

    async def position_quantity(self, symbol: str) -> int:
        """Get current position size for a symbol."""
        async with self._lock:
            position = self._find_position_locked(symbol)
            if position is None:
                return 0
            return position.quantity

    async def position_market_value(self, symbol: str) -> Decimal:
        """Get current market value (signed) for a symbol."""
        async with self._lock:
            position = self._find_position_locked(symbol)
            if position is None:
                return Decimal("0")
            return position.market_value

    async def symbol_daily_pnl(self, symbol: str) -> Decimal:
        """Get realized P&L for the symbol for the current trading day."""
        async with self._lock:
            return self._symbol_daily_pnl.get(symbol.upper(), Decimal("0"))

    async def symbol_realized_pnl(self, symbol: str) -> Decimal:
        """Get cumulative realized P&L for the symbol."""
        async with self._lock:
            return self._symbol_pnl.get(symbol.upper(), Decimal("0"))

    def _ensure_daily_rollover(self, current_date: date) -> None:
        """Reset daily P&L tracking when the date changes."""
        if self._loss_check_date != current_date:
            self._loss_check_date = current_date
            self.snapshot.realized_pnl_today = Decimal("0")
            self._symbol_daily_pnl.clear()


class SymbolLimits(BaseModel):
    """Per-symbol risk limits.

    Defines granular risk controls for individual symbols, allowing different
    risk parameters for different securities (e.g., stricter limits for volatile stocks).
    """

    symbol: str = Field(..., description="Trading symbol")
    max_position_size: int | None = Field(
        default=None, description="Max shares (overrides global limit)"
    )
    max_order_exposure: Decimal | None = Field(
        default=None, description="Max $ per order (overrides global limit)"
    )
    max_daily_loss: Decimal | None = Field(
        default=None, description="Max loss per day for this symbol"
    )
    max_correlation_exposure: Decimal | None = Field(
        default=None,
        description="Max notional exposure to highly correlated symbols",
    )

    model_config = ConfigDict(frozen=True)


class SymbolLimitRegistry:
    """Registry of per-symbol limits with configuration loading.

    Loads limits from JSON configuration file and provides fallback to default values.
    Supports hot-reloading when configuration file changes.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = config_path
        self.symbol_limits: dict[str, SymbolLimits] = {}
        self.default_limits: SymbolLimits | None = None

        if config_path and config_path.exists():
            self._load_config(config_path)

    def _load_config(self, path: Path) -> None:
        """Load symbol limits from JSON configuration file."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))

            # Load default limits
            default_data = data.get("default_limits", {})
            if default_data:
                default_position = default_data.get("max_position_size")
                default_exposure_raw = default_data.get("max_order_exposure")
                default_loss_raw = default_data.get("max_daily_loss")
                default_corr_raw = default_data.get("max_correlation_exposure")
                self.default_limits = SymbolLimits(
                    symbol="*DEFAULT*",
                    max_position_size=default_position if default_position is not None else None,
                    max_order_exposure=Decimal(str(default_exposure_raw))
                    if default_exposure_raw is not None
                    else None,
                    max_daily_loss=Decimal(str(default_loss_raw))
                    if default_loss_raw is not None
                    else None,
                    max_correlation_exposure=Decimal(str(default_corr_raw))
                    if default_corr_raw is not None
                    else None,
                )

            # Load per-symbol limits
            symbol_data = data.get("symbol_limits", {})
            for symbol, limits in symbol_data.items():
                position_limit = limits.get("max_position_size")
                exposure_raw = limits.get("max_order_exposure")
                daily_loss_raw = limits.get("max_daily_loss")
                corr_raw = limits.get("max_correlation_exposure")
                self.symbol_limits[symbol.upper()] = SymbolLimits(
                    symbol=symbol.upper(),
                    max_position_size=position_limit if position_limit is not None else None,
                    max_order_exposure=Decimal(str(exposure_raw))
                    if exposure_raw is not None
                    else None,
                    max_daily_loss=Decimal(str(daily_loss_raw))
                    if daily_loss_raw is not None
                    else None,
                    max_correlation_exposure=Decimal(str(corr_raw))
                    if corr_raw is not None
                    else None,
                )

            logger.info(
                "Loaded symbol limits for %d symbols from %s",
                len(self.symbol_limits),
                path,
            )
        except Exception as exc:
            logger.error("Failed to load symbol limits from %s: %s", path, exc)

    def get_limit(self, symbol: str) -> SymbolLimits | None:
        """Get limits for symbol, falling back to defaults.

        Args:
            symbol: Trading symbol

        Returns:
            SymbolLimits if found or default exists, None otherwise
        """
        symbol = symbol.upper()
        return self.symbol_limits.get(symbol, self.default_limits)

    def save_config(self, path: Path) -> None:
        """Save current limits to JSON configuration file."""
        data: dict[str, Any] = {}

        # Save default limits
        if self.default_limits:
            data["default_limits"] = {
                "max_position_size": self.default_limits.max_position_size,
                "max_order_exposure": str(self.default_limits.max_order_exposure)
                if self.default_limits.max_order_exposure
                else None,
                "max_daily_loss": str(self.default_limits.max_daily_loss)
                if self.default_limits.max_daily_loss
                else None,
                "max_correlation_exposure": str(self.default_limits.max_correlation_exposure)
                if self.default_limits.max_correlation_exposure
                else None,
            }

        # Save per-symbol limits
        data["symbol_limits"] = {}
        for symbol, limits in self.symbol_limits.items():
            data["symbol_limits"][symbol] = {
                "max_position_size": limits.max_position_size,
                "max_order_exposure": str(limits.max_order_exposure)
                if limits.max_order_exposure
                else None,
                "max_daily_loss": str(limits.max_daily_loss) if limits.max_daily_loss else None,
                "max_correlation_exposure": str(limits.max_correlation_exposure)
                if limits.max_correlation_exposure
                else None,
            }

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            logger.info("Saved symbol limits to %s", path)
        except Exception as exc:
            logger.error("Failed to save symbol limits to %s: %s", path, exc)

    def set_symbol_limit(
        self,
        symbol: str,
        max_position_size: int | None = None,
        max_order_exposure: Decimal | None = None,
        max_daily_loss: Decimal | None = None,
        max_correlation_exposure: Decimal | None = None,
    ) -> None:
        """Set or update limits for a symbol."""
        symbol = symbol.upper()
        self.symbol_limits[symbol] = SymbolLimits(
            symbol=symbol,
            max_position_size=max_position_size,
            max_order_exposure=max_order_exposure,
            max_daily_loss=max_daily_loss,
            max_correlation_exposure=max_correlation_exposure,
        )
        logger.info("Updated symbol limits for %s", symbol)

    def set_default_limit(
        self,
        *,
        max_position_size: int | None = None,
        max_order_exposure: Decimal | None = None,
        max_daily_loss: Decimal | None = None,
        max_correlation_exposure: Decimal | None = None,
    ) -> None:
        """Set default limits applied when a symbol-specific limit is not defined."""
        self.default_limits = SymbolLimits(
            symbol="*DEFAULT*",
            max_position_size=max_position_size,
            max_order_exposure=max_order_exposure,
            max_daily_loss=max_daily_loss,
            max_correlation_exposure=max_correlation_exposure,
        )
        logger.info("Updated default symbol limits")


class RiskGuard:
    """Additional risk validations for advanced strategies."""

    def __init__(
        self,
        portfolio: PortfolioState,
        max_exposure: Decimal,
        symbol_limits: SymbolLimitRegistry | None = None,
        correlation_guard: CorrelationRiskGuard | None = None,
    ) -> None:
        self.portfolio = portfolio
        self.max_exposure = max_exposure
        self.symbol_limits = symbol_limits
        self.correlation_guard = correlation_guard

    async def validate_order(
        self, contract: SymbolContract, side: OrderSide, quantity: int, price: Decimal
    ) -> None:
        await self.portfolio.check_daily_loss_limit()

        symbol_limits = (
            self.symbol_limits.get_limit(contract.symbol) if self.symbol_limits else None
        )
        exposure = price * quantity
        if symbol_limits is not None:
            await self._validate_symbol_limits(
                contract=contract,
                side=side,
                quantity=quantity,
                price=price,
                exposure=exposure,
                limits=symbol_limits,
            )

        if self.correlation_guard is not None:
            await self.correlation_guard.validate_order(
                contract=contract,
                side=side,
                quantity=quantity,
                price=price,
                portfolio=self.portfolio,
            )

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

    async def _validate_symbol_limits(
        self,
        *,
        contract: SymbolContract,
        side: OrderSide,
        quantity: int,
        price: Decimal,
        exposure: Decimal,
        limits: SymbolLimits,
    ) -> None:
        """Apply per-symbol risk rules."""
        # Position size check
        if limits.max_position_size is not None:
            current_qty = await self.portfolio.position_quantity(contract.symbol)
            if side == OrderSide.BUY:
                projected_qty = current_qty + quantity
            else:
                projected_qty = current_qty - quantity
            if abs(projected_qty) > limits.max_position_size:
                raise RuntimeError(
                    f"Projected position {projected_qty} exceeds per-symbol limit "
                    f"{limits.max_position_size} for {contract.symbol}"
                )

        # Order exposure check
        if (
            limits.max_order_exposure is not None
            and price > 0
            and exposure > limits.max_order_exposure
        ):
            raise RuntimeError(
                f"Order exposure {exposure} exceeds per-symbol exposure limit "
                f"{limits.max_order_exposure} for {contract.symbol}"
            )

        # Per-symbol daily loss check
        if limits.max_daily_loss is not None:
            symbol_daily_pnl = await self.portfolio.symbol_daily_pnl(contract.symbol)
            if symbol_daily_pnl <= -limits.max_daily_loss:
                raise RuntimeError(
                    f"Daily loss limit reached for {contract.symbol}: "
                    f"{symbol_daily_pnl} <= -{limits.max_daily_loss}"
                )
