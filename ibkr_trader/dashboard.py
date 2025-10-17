"""Real-time trading dashboard for live monitoring."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ibkr_trader.events import (
    EventBus,
    EventSubscription,
    EventTopic,
    ExecutionEvent,
    MarketDataEvent,
    OrderStatusEvent,
)
from ibkr_trader.portfolio import PortfolioState


class TradingDashboard:
    """Real-time dashboard displaying live trading activity.

    Features:
    - Live P&L and position updates
    - Recent order status changes
    - Market data feed
    - Risk indicators (daily loss, position limits)
    - Color-coded alerts
    """

    def __init__(
        self,
        event_bus: EventBus,
        portfolio: PortfolioState,
        max_position_size: int,
        max_daily_loss: Decimal,
    ) -> None:
        """Initialize dashboard.

        Args:
            event_bus: Event bus for subscribing to updates
            portfolio: Portfolio state tracker
            max_position_size: Maximum position size for warnings
            max_daily_loss: Daily loss limit for risk indicators
        """
        self.event_bus = event_bus
        self.portfolio = portfolio
        self.max_position_size = max_position_size
        self.max_daily_loss = max_daily_loss

        # State tracking
        self.recent_orders: list[dict[str, Any]] = []
        self.recent_executions: list[dict[str, Any]] = []
        self.market_prices: dict[str, Decimal] = {}
        self.last_update = datetime.now(UTC)

        # Subscriptions
        self._order_sub: EventSubscription | None = None
        self._execution_sub: EventSubscription | None = None
        self._market_sub: EventSubscription | None = None

        # Console
        self.console = Console()

    async def run(self) -> None:
        """Run the dashboard until interrupted."""
        # Subscribe to events
        self._order_sub = self.event_bus.subscribe(EventTopic.ORDER_STATUS)
        self._execution_sub = self.event_bus.subscribe(EventTopic.EXECUTION)
        self._market_sub = self.event_bus.subscribe(EventTopic.MARKET_DATA)

        # Start event processors
        tasks = [
            asyncio.create_task(self._process_orders()),
            asyncio.create_task(self._process_executions()),
            asyncio.create_task(self._process_market_data()),
        ]

        try:
            # Render loop
            with Live(
                self._build_layout(),
                console=self.console,
                refresh_per_second=2,
                screen=True,
            ) as live:
                while True:
                    live.update(self._build_layout())
                    await asyncio.sleep(0.5)
        except KeyboardInterrupt:
            self.console.print("\n[yellow]Dashboard stopped by user[/yellow]")
        finally:
            # Cancel background tasks
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

            # Cleanup subscriptions
            if self._order_sub:
                self._order_sub.close()
            if self._execution_sub:
                self._execution_sub.close()
            if self._market_sub:
                self._market_sub.close()

    def _build_layout(self) -> Layout:
        """Build the dashboard layout.

        Returns:
            Rich Layout with all panels
        """
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body"),
            Layout(name="footer", size=3),
        )

        layout["header"].update(self._build_header())
        layout["body"].split_row(
            Layout(self._build_account_panel(), name="left"),
            Layout(name="right"),
        )
        layout["body"]["right"].split_column(
            Layout(self._build_positions_panel(), name="positions"),
            Layout(self._build_activity_panel(), name="activity"),
        )
        layout["footer"].update(self._build_footer())

        return layout

    def _build_header(self) -> Panel:
        """Build header panel with title and timestamp."""
        title = Text("IBKR Trading Dashboard", style="bold white on blue", justify="center")
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        return Panel(
            f"{title}\n{timestamp}",
            border_style="blue",
        )

    def _build_account_panel(self) -> Panel:
        """Build account summary panel."""
        snapshot = self.portfolio.snapshot

        # Calculate unrealized P&L (simplified)
        unrealized_pnl = Decimal("0")
        for symbol, position in snapshot.positions.items():
            if symbol in self.market_prices:
                current_price = self.market_prices[symbol]
                cost_basis = position.avg_price * position.quantity
                market_value = current_price * position.quantity
                unrealized_pnl += market_value - cost_basis

        # Risk indicator
        daily_loss = snapshot.realized_pnl_today
        loss_pct = (
            float(abs(daily_loss) / self.max_daily_loss * 100) if self.max_daily_loss > 0 else 0.0
        )

        if loss_pct >= 90:
            risk_color = "red"
            risk_status = "CRITICAL"
        elif loss_pct >= 80:
            risk_color = "yellow"
            risk_status = "WARNING"
        else:
            risk_color = "green"
            risk_status = "OK"

        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column(style="bold")
        table.add_column(justify="right")

        table.add_row("Net Liquidation:", f"${snapshot.net_liquidation:,.2f}")
        table.add_row("Cash:", f"${snapshot.total_cash:,.2f}")
        table.add_row("Buying Power:", f"${snapshot.buying_power:,.2f}")
        table.add_row(
            "Realized P&L (Today):",
            f"[{'green' if daily_loss >= 0 else 'red'}]${daily_loss:,.2f}[/]",
        )
        table.add_row(
            "Unrealized P&L:",
            f"[{'green' if unrealized_pnl >= 0 else 'red'}]${unrealized_pnl:,.2f}[/]",
        )
        table.add_row("Risk Status:", f"[{risk_color}]{risk_status}[/] ({loss_pct:.0f}%)")

        return Panel(table, title="Account Summary", border_style="green")

    def _build_positions_panel(self) -> Panel:
        """Build positions table panel."""
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Symbol", style="bold")
        table.add_column("Qty", justify="right")
        table.add_column("Avg Price", justify="right")
        table.add_column("Market Price", justify="right")
        table.add_column("P&L", justify="right")
        table.add_column("Size Warning", justify="center")

        if not self.portfolio.snapshot.positions:
            table.add_row("No positions", "", "", "", "", "")
        else:
            for symbol, position in self.portfolio.snapshot.positions.items():
                qty = position.quantity
                avg_price = position.avg_price
                market_price = self.market_prices.get(symbol, avg_price)

                # Calculate P&L
                cost_basis = avg_price * qty
                market_value = market_price * qty
                pnl = market_value - cost_basis
                pnl_color = "green" if pnl >= 0 else "red"

                # Size warning
                size_pct = abs(qty) / self.max_position_size * 100
                if size_pct >= 90:
                    warning = "[red]⚠[/red]"
                elif size_pct >= 80:
                    warning = "[yellow]⚠[/yellow]"
                else:
                    warning = ""

                table.add_row(
                    symbol,
                    str(qty),
                    f"${avg_price:.2f}",
                    f"${market_price:.2f}",
                    f"[{pnl_color}]${pnl:,.2f}[/]",
                    warning,
                )

        return Panel(table, title="Positions", border_style="cyan")

    def _build_activity_panel(self) -> Panel:
        """Build recent activity panel."""
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Time", style="dim")
        table.add_column("Type")
        table.add_column("Symbol")
        table.add_column("Details")

        # Combine and sort recent events
        events: list[tuple[datetime, str, str, str]] = []

        for order in self.recent_orders[-10:]:
            events.append(
                (
                    order["timestamp"],
                    "ORDER",
                    order["symbol"],
                    f"{order['status']} {order['side']} {order['filled']}/{order['quantity']}",
                )
            )

        for execution in self.recent_executions[-10:]:
            events.append(
                (
                    execution["timestamp"],
                    "FILL",
                    execution["symbol"],
                    f"{execution['side']} {execution['quantity']}@${execution['price']:.2f}",
                )
            )

        # Sort by time (most recent first)
        events.sort(key=lambda x: x[0], reverse=True)

        if not events:
            table.add_row("--:--:--", "No activity", "", "")
        else:
            for timestamp, event_type, symbol, details in events[:15]:
                time_str = timestamp.strftime("%H:%M:%S")
                table.add_row(time_str, event_type, symbol, details)

        return Panel(table, title="Recent Activity", border_style="magenta")

    def _build_footer(self) -> Panel:
        """Build footer with keybindings."""
        return Panel(
            "[bold]Ctrl+C[/bold]: Exit  |  Auto-refresh: 2Hz",
            border_style="blue",
        )

    async def _process_orders(self) -> None:
        """Background task processing order status events."""
        if self._order_sub is None:
            return

        try:
            async for event in self._order_sub:
                if isinstance(event, OrderStatusEvent):
                    self.recent_orders.append(
                        {
                            "timestamp": event.timestamp,
                            "symbol": event.contract.symbol,
                            "status": event.status.value,
                            "side": event.side.value,
                            "quantity": event.filled + event.remaining,
                            "filled": event.filled,
                        }
                    )
                    # Keep only last 50
                    if len(self.recent_orders) > 50:
                        self.recent_orders.pop(0)
        except asyncio.CancelledError:
            pass

    async def _process_executions(self) -> None:
        """Background task processing execution events."""
        if self._execution_sub is None:
            return

        try:
            async for event in self._execution_sub:
                if isinstance(event, ExecutionEvent):
                    self.recent_executions.append(
                        {
                            "timestamp": event.timestamp,
                            "symbol": event.contract.symbol,
                            "side": event.side.value,
                            "quantity": event.quantity,
                            "price": event.price,
                        }
                    )
                    # Keep only last 50
                    if len(self.recent_executions) > 50:
                        self.recent_executions.pop(0)
        except asyncio.CancelledError:
            pass

    async def _process_market_data(self) -> None:
        """Background task processing market data events."""
        if self._market_sub is None:
            return

        try:
            async for event in self._market_sub:
                if isinstance(event, MarketDataEvent):
                    self.market_prices[event.symbol] = event.price
                    self.last_update = event.timestamp
        except asyncio.CancelledError:
            pass
