"""Real-time trading dashboard for live monitoring."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ibkr_trader.core.alerting import AlertMessage, AlertSeverity
from ibkr_trader.core.kill_switch import KillSwitch
from ibkr_trader.events import (
    DiagnosticEvent,
    EventBus,
    EventSubscription,
    EventTopic,
    ExecutionEvent,
    MarketDataEvent,
    OrderStatusEvent,
)
from ibkr_trader.portfolio import PortfolioState, SymbolLimitRegistry


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
        symbol_limits: SymbolLimitRegistry | None = None,
        kill_switch: KillSwitch | None = None,
    ) -> None:
        """Initialize dashboard.

        Args:
            event_bus: Event bus for subscribing to updates
            portfolio: Portfolio state tracker
            max_position_size: Maximum position size for warnings
            max_daily_loss: Daily loss limit for risk indicators
            symbol_limits: Optional per-symbol limit registry for utilization display
        """
        self.event_bus = event_bus
        self.portfolio = portfolio
        self.max_position_size = max_position_size
        self.max_daily_loss = max_daily_loss
        self.symbol_limits = symbol_limits
        self._kill_switch = kill_switch
        self._kill_switch_engaged = kill_switch.is_engaged() if kill_switch else False
        self._kill_switch_triggered_at: datetime | None = None
        if self._kill_switch_engaged and kill_switch is not None:
            state = kill_switch.status()
            if state.triggered_at:
                try:
                    self._kill_switch_triggered_at = datetime.fromisoformat(state.triggered_at)
                except ValueError:
                    self._kill_switch_triggered_at = datetime.now(UTC)

        # State tracking
        self.recent_orders: list[dict[str, Any]] = []
        self.recent_executions: list[dict[str, Any]] = []
        self.market_prices: dict[str, Decimal] = {}
        self.last_update = datetime.now(UTC)
        self._latest_screener_symbols: list[str] = []
        self._latest_screener_timestamp: datetime | None = None
        self._recent_alerts: list[dict[str, object]] = []

        # Subscriptions
        self._order_sub: EventSubscription | None = None
        self._execution_sub: EventSubscription | None = None
        self._market_sub: EventSubscription | None = None
        self._diagnostic_sub: EventSubscription | None = None
        self._alert_sub: EventSubscription | None = None

        # Console
        self.console = Console()

    async def run(self) -> None:
        """Run the dashboard until interrupted."""
        # Subscribe to events
        self._order_sub = self.event_bus.subscribe(EventTopic.ORDER_STATUS)
        self._execution_sub = self.event_bus.subscribe(EventTopic.EXECUTION)
        self._market_sub = self.event_bus.subscribe(EventTopic.MARKET_DATA)
        self._diagnostic_sub = self.event_bus.subscribe(EventTopic.DIAGNOSTIC)
        self._alert_sub = self.event_bus.subscribe(EventTopic.ALERT)

        # Start event processors
        tasks = [
            asyncio.create_task(self._process_orders()),
            asyncio.create_task(self._process_executions()),
            asyncio.create_task(self._process_market_data()),
            asyncio.create_task(self._process_diagnostics()),
            asyncio.create_task(self._process_alerts()),
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
            if self._diagnostic_sub:
                self._diagnostic_sub.close()
            if self._alert_sub:
                self._alert_sub.close()

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
            Layout(self._build_screener_panel(), name="screener"),
            Layout(self._build_alerts_panel(), name="alerts"),
            Layout(self._build_activity_panel(), name="activity"),
        )
        layout["footer"].update(self._build_footer())

        return layout

    def _build_header(self) -> Panel:
        """Build header panel with title and timestamp."""
        title = Text("IBKR Trading Dashboard", style="bold white on blue", justify="center")
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        header_lines = [f"{title}", timestamp]
        if self._kill_switch_engaged:
            engaged_at = (
                self._kill_switch_triggered_at.strftime("%Y-%m-%d %H:%M:%S UTC")
                if self._kill_switch_triggered_at
                else "unknown"
            )
            header_lines.append(f"[red]KILL SWITCH ENGAGED[/red] (since {engaged_at})")
        return Panel("\n".join(header_lines), border_style="blue")

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

        # Risk indicator, including kill-switch buffer and fee-adjusted metrics
        gross_realized = snapshot.realized_pnl_today
        realized_costs = snapshot.realized_costs_today
        net_realized = gross_realized - realized_costs
        loss_consumed = max(Decimal("0"), -gross_realized)
        if self.max_daily_loss > 0:
            loss_pct = float((loss_consumed / self.max_daily_loss) * Decimal("100"))
        else:
            loss_pct = 0.0

        if self.max_daily_loss <= 0:
            risk_color = "green"
            risk_status = "DISABLED"
        elif loss_consumed >= self.max_daily_loss:
            risk_color = "red"
            risk_status = "TRIGGERED"
        elif loss_pct >= 80:
            risk_color = "yellow"
            risk_status = "ARMED"
        else:
            risk_color = "green"
            risk_status = "OK"

        remaining_buffer = (
            self.max_daily_loss - loss_consumed if self.max_daily_loss > 0 else Decimal("0")
        )

        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column(style="bold")
        table.add_column(justify="right")

        table.add_row("Net Liquidation:", f"${snapshot.net_liquidation:,.2f}")
        table.add_row("Cash:", f"${snapshot.total_cash:,.2f}")
        table.add_row("Buying Power:", f"${snapshot.buying_power:,.2f}")
        table.add_row(
            "Gross Realized P&L:",
            f"[{'green' if gross_realized >= 0 else 'red'}]${gross_realized:,.2f}[/]",
        )
        table.add_row(
            "Estimated Costs:",
            f"[{'red' if realized_costs > 0 else 'green'}]${realized_costs:,.2f}[/]",
        )
        table.add_row(
            "Net Realized P&L:",
            f"[{'green' if net_realized >= 0 else 'red'}]${net_realized:,.2f}[/]",
        )
        table.add_row(
            "Unrealized P&L:",
            f"[{'green' if unrealized_pnl >= 0 else 'red'}]${unrealized_pnl:,.2f}[/]",
        )
        table.add_row("Risk Status:", f"[{risk_color}]{risk_status}[/] ({loss_pct:.0f}%)")
        if self.max_daily_loss > 0:
            table.add_row(
                "Kill Switch Buffer:",
                (
                    f"[{'green' if remaining_buffer > 0 else 'red'}]"
                    f"${remaining_buffer:,.2f} remaining[/]"
                ),
            )

        return Panel(table, title="Account Summary", border_style="green")

    def _build_positions_panel(self) -> Panel:
        """Build positions table panel."""
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Symbol", style="bold")
        table.add_column("Qty", justify="right")
        table.add_column("Avg Price", justify="right")
        table.add_column("Market Price", justify="right")
        table.add_column("P&L", justify="right")
        table.add_column("Limit Util", justify="center")

        has_symbol_specific_limit = False

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

                # Determine limit utilization
                limit_entry = (
                    self.symbol_limits.get_limit(symbol) if self.symbol_limits is not None else None
                )
                limit_override = bool(
                    limit_entry
                    and limit_entry.symbol != "*DEFAULT*"
                    and limit_entry.max_position_size
                )
                limit_value: int | None = None
                if limit_entry and limit_entry.max_position_size is not None:
                    limit_value = limit_entry.max_position_size
                elif self.max_position_size:
                    limit_value = self.max_position_size

                warning = ""
                if limit_value and limit_value > 0:
                    size_pct = abs(qty) / limit_value * 100
                    if size_pct >= 100:
                        warning = "[red]⚠[/red]"
                        color = "red"
                    elif size_pct >= 80:
                        warning = "[yellow]⚠[/yellow]"
                        color = "yellow"
                    else:
                        color = "green"
                    suffix = " *" if limit_override else ""
                    limit_text = f"[{color}]{abs(qty)}/{limit_value} ({size_pct:.0f}%){suffix}[/]"
                    if warning:
                        limit_text = f"{limit_text} {warning}"
                else:
                    limit_text = "--"

                if limit_override:
                    has_symbol_specific_limit = True

                table.add_row(
                    symbol,
                    str(qty),
                    f"${avg_price:.2f}",
                    f"${market_price:.2f}",
                    f"[{pnl_color}]${pnl:,.2f}[/]",
                    limit_text,
                )

        subtitle = "* denotes symbol-specific limit" if has_symbol_specific_limit else None
        return Panel(table, title="Positions", border_style="cyan", subtitle=subtitle)

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

    def _build_screener_panel(self) -> Panel:
        """Build screener universe panel sourced from telemetry events."""
        table = Table(show_header=True, header_style="bold blue")
        table.add_column("Symbol", style="bold")

        if not self._latest_screener_symbols:
            table.add_row("No screener data")
            subtitle = "Awaiting *.screen_refresh telemetry"
        else:
            for symbol in self._latest_screener_symbols:
                table.add_row(symbol)
            if self._latest_screener_timestamp:
                subtitle = f"Last refresh: {self._latest_screener_timestamp:%H:%M:%S}"
            else:
                subtitle = "Last refresh: --"

        return Panel(table, title="Screener Universe", border_style="blue", subtitle=subtitle)

    def _build_footer(self) -> Panel:
        """Build footer with keybindings."""
        return Panel(
            "[bold]Ctrl+C[/bold]: Exit  |  Auto-refresh: 2Hz",
            border_style="blue",
        )

    def _build_alerts_panel(self) -> Panel:
        """Build alert panel showing recent escalations."""

        table = Table(show_header=True, header_style="bold red")
        table.add_column("Time", style="dim")
        table.add_column("Severity")
        table.add_column("Title")

        if not self._recent_alerts:
            table.add_row("--:--:--", "--", "No alerts")
        else:
            for alert in self._recent_alerts[-8:][::-1]:
                timestamp = alert["timestamp"].strftime("%H:%M:%S")
                severity = alert["severity"].upper()
                title = alert["title"]
                color = {
                    "CRITICAL": "red",
                    "WARNING": "yellow",
                    "INFO": "green",
                }.get(severity, "white")
                table.add_row(timestamp, f"[{color}]{severity}[/]", title)

        return Panel(table, title="Alerts", border_style="red")

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

    async def _process_diagnostics(self) -> None:
        """Background task processing diagnostic telemetry events."""
        if self._diagnostic_sub is None:
            return

        try:
            async for event in self._diagnostic_sub:
                if isinstance(event, DiagnosticEvent):
                    self._handle_diagnostic_event(event)
        except asyncio.CancelledError:
            pass

    async def _process_alerts(self) -> None:
        """Background task capturing alert events."""
        if self._alert_sub is None:
            return

        try:
            async for event in self._alert_sub:
                if isinstance(event, AlertMessage):
                    self._handle_alert_event(event)
        except asyncio.CancelledError:
            pass

    def _handle_diagnostic_event(self, event: DiagnosticEvent) -> None:
        """Process a single diagnostic event (extracted for testability)."""
        if event.message.endswith("screen_refresh"):
            context = event.context or {}
            symbols_raw = context.get("symbols") or []
            self._latest_screener_symbols = [str(symbol).upper() for symbol in symbols_raw]
            generated_at = context.get("generated_at") or context.get("timestamp")
            if isinstance(generated_at, str):
                with suppress(ValueError):
                    self._latest_screener_timestamp = datetime.fromisoformat(generated_at)
            else:
                self._latest_screener_timestamp = event.timestamp

    def _handle_alert_event(self, alert: AlertMessage) -> None:
        record = {
            "timestamp": alert.timestamp,
            "severity": alert.severity.value,
            "title": alert.title,
        }
        self._recent_alerts.append(record)
        if len(self._recent_alerts) > 50:
            del self._recent_alerts[:-50]
        if alert.severity == AlertSeverity.CRITICAL:
            self._kill_switch_engaged = True
            self._kill_switch_triggered_at = alert.timestamp
