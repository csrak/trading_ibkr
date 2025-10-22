"""Monitoring and diagnostics commands for IBKR Trader CLI."""

import asyncio
import getpass
import json
import time
from contextlib import suppress
from datetime import UTC, datetime
from decimal import Decimal
from json import JSONDecodeError

import typer
from loguru import logger

from ibkr_trader.broker import IBKRBroker
from ibkr_trader.config import load_config
from ibkr_trader.constants import DEFAULT_PORTFOLIO_SNAPSHOT
from ibkr_trader.events import DiagnosticEvent, EventBus, EventTopic
from ibkr_trader.market_data import MarketDataService, SubscriptionRequest
from ibkr_trader.models import SymbolContract
from ibkr_trader.safety import LiveTradingGuard
from ibkr_trader.summary import summarize_run
from model.data import FileCacheStore, IBKRMarketDataSource, OptionChainCacheStore

from .utils import (
    build_portfolio_and_risk_guard,
    build_telemetry_alert_router,
    format_seconds,
    format_telemetry_line,
    load_kill_switch,
    setup_logging,
    tail_telemetry_entries,
)

monitoring_app = typer.Typer(
    name="monitoring",
    help="Monitoring and diagnostics commands",
)


@monitoring_app.command()
def diagnostics(
    show_metadata: bool = typer.Option(
        False,
        "--show-metadata",
        help="Display individual option chain cache entries.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
) -> None:
    """Display cache TTL and rate limiter diagnostics."""

    config = load_config()
    setup_logging(config.log_dir, verbose)

    price_cache_dir = config.training_cache_dir
    price_cache_dir.mkdir(parents=True, exist_ok=True)
    price_cache = FileCacheStore(price_cache_dir)
    option_cache_dir = config.training_cache_dir / "option_chains"
    option_cache_dir.mkdir(parents=True, exist_ok=True)
    option_cache = OptionChainCacheStore(option_cache_dir)

    ib_source = IBKRMarketDataSource(
        max_snapshots_per_session=config.training_max_snapshots,
        min_request_interval_seconds=config.training_snapshot_interval,
    )
    used, limit = ib_source.rate_limit_usage

    typer.echo("=== Market Data Diagnostics ===")
    typer.echo(
        f"Price cache directory: {price_cache_dir} (ttl={format_seconds(price_cache.ttl_seconds)})"
    )
    typer.echo(
        "Option chain cache directory: "
        f"{option_cache_dir} (ttl={format_seconds(option_cache.max_age_seconds)})"
    )
    typer.echo(f"IBKR rate limit usage: {used}/{limit} requests this session")

    if show_metadata:
        entries = option_cache.metadata_entries()
        if not entries:
            typer.echo("No option chain metadata entries found.")
        else:
            typer.echo("\nOption chain cache entries:")
            for entry in entries:
                age_str = (
                    format_seconds(entry["age_seconds"])
                    if entry.get("age_seconds") is not None
                    else "n/a"
                )
                schema = entry.get("schema_version")
                typer.echo(
                    f"  {entry['symbol']} {entry['expiry']} | age={age_str} | schema={schema}"
                )


@monitoring_app.command("session-status")
def session_status(
    tail: int = typer.Option(5, "--tail", min=0, help="Telemetry entries to display"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
) -> None:
    """Show current portfolio snapshot and recent telemetry events."""

    config = load_config()
    setup_logging(config.log_dir, verbose)

    snapshot_path = config.data_dir / DEFAULT_PORTFOLIO_SNAPSHOT.name
    telemetry_file = config.log_dir / "telemetry.jsonl"

    typer.echo("=== Session Status ===")
    typer.echo(f"Snapshot file: {snapshot_path}")

    summary = summarize_run(snapshot_path, tail_telemetry_entries(telemetry_file, tail or 100))
    typer.echo(summary.headline())

    if summary.raw_snapshot is None:
        typer.echo("Portfolio snapshot not available.")
    else:
        positions = summary.raw_snapshot.get("positions") or {}
        if positions:
            typer.echo("Positions:")
            for symbol, details in positions.items():
                qty = details.get("quantity") if isinstance(details, dict) else details
                typer.echo(f"  {symbol}: {qty}")
        else:
            typer.echo("No open positions recorded.")

    typer.echo("")
    typer.echo(f"Telemetry file: {telemetry_file}")
    if not summary.telemetry_warnings:
        typer.echo("No recent telemetry warnings.")
    else:
        typer.echo("Recent telemetry warnings:")
        for line in summary.telemetry_warnings:
            typer.echo(f"  {line}")


@monitoring_app.command("monitor-telemetry")
def monitor_telemetry(
    tail: int = typer.Option(
        20,
        "--tail",
        min=0,
        help="Number of most recent telemetry entries to display (0 = show all)",
    ),
    follow: bool = typer.Option(
        False,
        "--follow/--no-follow",
        help="Continue watching for new telemetry entries",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
) -> None:
    """Print telemetry records collected by the platform."""

    config = load_config()
    setup_logging(config.log_dir, verbose)

    telemetry_file = config.log_dir / "telemetry.jsonl"
    if not telemetry_file.exists():
        typer.echo(f"No telemetry file found at {telemetry_file}")
        raise typer.Exit()

    typer.echo(f"Telemetry file: {telemetry_file}")

    try:
        entries = tail_telemetry_entries(telemetry_file, tail)
        for entry in entries:
            typer.echo(entry)

        if not follow:
            return

        with telemetry_file.open("r", encoding="utf-8") as handle:
            handle.seek(0, 2)
            while True:
                line = handle.readline()
                if not line:
                    time.sleep(0.5)
                    continue
                formatted = format_telemetry_line(line)
                if formatted:
                    typer.echo(formatted)
    except KeyboardInterrupt:  # pragma: no cover - user initiated
        typer.echo("Stopping telemetry monitor.")


@monitoring_app.command()
def dashboard(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
) -> None:
    """Launch real-time trading dashboard with live P&L monitoring."""
    config = load_config()
    setup_logging(config.log_dir, verbose)

    asyncio.run(run_dashboard(config))


async def run_dashboard(config: "IBKRConfig") -> None:  # noqa: F821
    """Run dashboard asynchronously."""
    from ibkr_trader.dashboard import TradingDashboard

    # Initialize components
    event_bus = EventBus()
    guard = LiveTradingGuard(config=config, live_flag_enabled=False)
    portfolio, risk_guard, symbol_limits = build_portfolio_and_risk_guard(config)
    kill_switch = load_kill_switch(config)
    if kill_switch.is_engaged():
        state = kill_switch.status()
        logger.error(
            "Kill switch is engaged ({}). Resolve before launching the dashboard.",
            state.alert_title or "unknown alert",
        )
        return
    alert_router = build_telemetry_alert_router(
        config,
        event_bus,
        kill_switch=kill_switch,
        session_context={"source": "dashboard"},
        history_path=config.log_dir / "alerts_history.jsonl",
    )
    broker = IBKRBroker(
        config=config,
        guard=guard,
        event_bus=event_bus,
        risk_guard=risk_guard,
    )
    market_data = MarketDataService(event_bus=event_bus)

    try:
        await alert_router.start()
        # Connect to IBKR
        await broker.connect()
        logger.info("Connected to IBKR - loading account data...")

        # Load initial state
        account_summary = await broker.get_account_summary()
        await portfolio.update_account(account_summary)
        positions = await broker.get_positions()
        await portfolio.update_positions(positions)

        # Subscribe to market data for all positions
        if not config.use_mock_market_data:
            market_data.attach_ib(broker.ib)
            for symbol in portfolio.snapshot.positions:
                request = SubscriptionRequest(SymbolContract(symbol=symbol))
                context = market_data.subscribe(request)
                await context.__aenter__()

        # Create and run dashboard
        dash = TradingDashboard(
            event_bus=event_bus,
            portfolio=portfolio,
            max_position_size=config.max_position_size,
            max_daily_loss=Decimal(str(config.max_daily_loss)),
            symbol_limits=symbol_limits,
            kill_switch=kill_switch,
        )

        logger.info("Starting dashboard...")
        await dash.run()

    except KeyboardInterrupt:
        logger.info("Dashboard stopped by user")
    except Exception as e:
        logger.error(f"Dashboard error: {e}")
        raise
    finally:
        with suppress(Exception):
            await alert_router.stop()
        await broker.disconnect()


@monitoring_app.command("test-alerts")
def test_alerts(
    rate_limit_events: int = typer.Option(
        5,
        "--rate-limit-events",
        "-r",
        min=1,
        help="Number of synthetic trailing stop rate-limit events to emit.",
    ),
    screener_namespace: str = typer.Option(
        "test_screener",
        "--screener-namespace",
        "-s",
        help="Telemetry namespace used for screener refresh events.",
    ),
    include_screener: bool = typer.Option(
        True,
        "--include-screener/--no-screener",
        help="Whether to emit a screener refresh followed by a stall alert.",
    ),
    engage_kill_switch: bool = typer.Option(
        False,
        "--engage-kill-switch/--no-engage-kill-switch",
        help="Engage the persistent kill switch when critical alerts fire (default: off).",
    ),
) -> None:
    """Emit synthetic telemetry to exercise alert routing."""

    config = load_config()
    setup_logging(config.log_dir, verbose=False)

    event_bus = EventBus()
    kill_switch = load_kill_switch(config) if engage_kill_switch else None
    alert_router = build_telemetry_alert_router(
        config,
        event_bus,
        kill_switch=kill_switch,
        enable_kill_switch=engage_kill_switch,
        session_context={"source": "synthetic_alerts"},
        history_path=config.log_dir / "alerts_history.jsonl",
    )

    async def _run() -> None:
        await alert_router.start()

        # trailing stop rate-limit events
        for _ in range(rate_limit_events):
            event = DiagnosticEvent(
                level="WARNING",
                message="trailing_stop.rate_limited",
                timestamp=datetime.now(tz=UTC),
                context={
                    "stop_id": "TEST_STOP",
                    "symbol": "TEST",
                },
            )
            await event_bus.publish(EventTopic.DIAGNOSTIC, event)
            await asyncio.sleep(0)

        if include_screener:
            refresh_event = DiagnosticEvent(
                level="INFO",
                message=f"{screener_namespace}.screen_refresh",
                timestamp=datetime.now(tz=UTC),
                context={"symbols": ["TEST"], "generated_at": datetime.now(tz=UTC).isoformat()},
            )
            await event_bus.publish(EventTopic.DIAGNOSTIC, refresh_event)
            # wait long enough to trigger stale alert
            stale_delay = max(config.screener_alert_stale_seconds, 1)
            await asyncio.sleep(min(stale_delay + 1, 5))

        await asyncio.sleep(0.5)
        await alert_router.stop()

    logger.info("Dispatching synthetic telemetry alerts …")
    try:
        asyncio.run(_run())
        logger.info(
            "Synthetic alert run complete. Check central alerting destination for delivery."
        )
    except KeyboardInterrupt:  # pragma: no cover - interactive use
        logger.warning("Synthetic alert run interrupted.")


@monitoring_app.command("kill-switch-status")
def kill_switch_status() -> None:
    """Display current kill switch information."""

    config = load_config()
    state = load_kill_switch(config).status()
    typer.echo("Kill Switch Status")
    typer.echo("====================")
    typer.echo(f"Engaged: {state.engaged}")
    typer.echo(f"Triggered At: {state.triggered_at or 'N/A'}")
    typer.echo(f"Alert Title: {state.alert_title or 'N/A'}")
    typer.echo(f"Severity: {state.severity or 'N/A'}")
    typer.echo(f"Acknowledged: {state.acknowledged}")
    typer.echo(f"Acknowledged By: {state.acknowledged_by or 'N/A'}")
    typer.echo(f"Acknowledged At: {state.acknowledged_at or 'N/A'}")
    typer.echo(f"Note: {state.note or 'N/A'}")
    typer.echo(f"Context: {state.context or {}}")


@monitoring_app.command("kill-switch-clear")
def kill_switch_clear(
    note: str = typer.Option(None, "--note", help="Optional acknowledgement note."),
    operator: str = typer.Option(  # noqa: B008 - Typer handles callable defaults
        getpass.getuser,
        "--operator",
        help="Operator acknowledging the kill switch (default: current user).",
    ),
) -> None:
    """Clear the kill switch after confirming conditions are safe."""

    config = load_config()
    ks = load_kill_switch(config)
    if not ks.is_engaged():
        typer.echo("Kill switch is not engaged.")
        return

    if ks.clear(acknowledged_by=operator, note=note):
        typer.echo("Kill switch cleared. Trading may be resumed once checks are complete.")
    else:
        typer.echo("Kill switch was already cleared.")


@monitoring_app.command("alert-history")
def alert_history(
    limit: int = typer.Option(
        20, "--limit", "-n", min=1, help="Number of alert entries to display (newest last)."
    ),
    follow: bool = typer.Option(
        False,
        "--follow",
        "-f",
        help="Continuously stream new alert entries (Ctrl+C to stop).",
    ),
    poll_seconds: float = typer.Option(
        1.0, "--interval", "-i", min=0.25, help="Polling interval when following alerts."
    ),
    severity: list[str] = typer.Option(
        None,
        "--severity",
        "-s",
        help="Filter by severity (INFO, WARNING, CRITICAL). Can be passed multiple times.",
    ),
    source: list[str] = typer.Option(
        None,
        "--source",
        help="Filter by alert source (matches context['source']).",
    ),
    session_id: list[str] = typer.Option(
        None,
        "--session-id",
        help="Filter by session_id present in alert context.",
    ),
    json_only: bool = typer.Option(
        False,
        "--json",
        help="Emit alerts as compact JSON rather than raw log lines.",
    ),
) -> None:
    """Display recent alert history entries."""

    config = load_config()
    history_path = config.log_dir / "alerts_history.jsonl"
    if not history_path.exists():
        typer.echo("No alert history found.")
        return

    try:
        with history_path.open("r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except Exception as exc:  # pragma: no cover - IO failure
        typer.echo(f"Failed to read alert history: {exc}")
        return

    filters = {
        "severity": {level.upper() for level in severity} if severity else None,
        "source": set(source) if source else None,
        "session_id": set(session_id) if session_id else None,
    }

    def _match(entry: dict[str, object]) -> bool:
        if filters["severity"] and entry.get("severity", "").upper() not in filters["severity"]:
            return False
        context = entry.get("context") or {}
        if filters["source"] and (
            not isinstance(context, dict) or context.get("source") not in filters["source"]
        ):
            return False
        return not (
            filters["session_id"]
            and (
                not isinstance(context, dict)
                or context.get("session_id") not in filters["session_id"]
            )
        )

    def _render(entry: dict[str, object], raw: str) -> str:
        if json_only:
            return json.dumps(entry, separators=(",", ":"))
        return raw.rstrip("\n")

    matched_lines: list[str] = []
    for line in lines[-limit:]:
        try:
            parsed = json.loads(line)
        except JSONDecodeError:
            parsed = None
        if parsed is None:
            if not filters["severity"] and not filters["source"] and not filters["session_id"]:
                matched_lines.append(line.rstrip("\n"))
            continue
        if _match(parsed):
            matched_lines.append(_render(parsed, line))

    typer.echo(f"Showing {len(matched_lines)} alert entries (newest last):")
    for line in matched_lines:
        typer.echo(line)

    if not follow:
        return

    typer.echo("\n-- Following new alerts (Ctrl+C to stop) --")
    try:
        with history_path.open("r", encoding="utf-8") as handle:
            handle.seek(0, 2)  # move to end of file
            while True:
                position = handle.tell()
                line = handle.readline()
                if line:
                    try:
                        parsed = json.loads(line)
                    except JSONDecodeError:
                        parsed = None
                    if parsed is not None and not _match(parsed):
                        continue
                    output = _render(parsed, line) if parsed is not None else line.rstrip("\n")
                    typer.echo(output)
                else:
                    handle.seek(position)
                    time.sleep(poll_seconds)
    except KeyboardInterrupt:  # pragma: no cover - interactive use
        typer.echo("\nStopped following alerts.")
    except Exception as exc:  # pragma: no cover - IO failure
        typer.echo(f"\nStopped following alerts (error: {exc}).")
