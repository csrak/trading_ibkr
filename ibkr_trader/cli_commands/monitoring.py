"""Monitoring and diagnostics commands for IBKR Trader CLI."""

import asyncio
import time
from decimal import Decimal

import typer
from loguru import logger

from ibkr_trader.broker import IBKRBroker
from ibkr_trader.config import load_config
from ibkr_trader.constants import DEFAULT_PORTFOLIO_SNAPSHOT
from ibkr_trader.events import EventBus
from ibkr_trader.market_data import MarketDataService, SubscriptionRequest
from ibkr_trader.models import SymbolContract
from ibkr_trader.safety import LiveTradingGuard
from ibkr_trader.summary import summarize_run
from model.data import FileCacheStore, IBKRMarketDataSource, OptionChainCacheStore

from .utils import (
    build_portfolio_and_risk_guard,
    format_seconds,
    format_telemetry_line,
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
    broker = IBKRBroker(
        config=config,
        guard=guard,
        event_bus=event_bus,
        risk_guard=risk_guard,
    )
    market_data = MarketDataService(event_bus=event_bus)

    try:
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
        )

        logger.info("Starting dashboard...")
        await dash.run()

    except KeyboardInterrupt:
        logger.info("Dashboard stopped by user")
    except Exception as e:
        logger.error(f"Dashboard error: {e}")
        raise
    finally:
        await broker.disconnect()
