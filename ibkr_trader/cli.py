"""CLI entry point for IBKR Personal Trader."""

import asyncio
import json
import sys
import time
from collections import deque
from contextlib import AbstractAsyncContextManager, suppress
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd
import typer
from loguru import logger

from ibkr_trader.backtest.engine import BacktestEngine
from ibkr_trader.broker import IBKRBroker
from ibkr_trader.config import TradingMode, load_config
from ibkr_trader.constants import (
    DEFAULT_PORTFOLIO_SNAPSHOT,
    MARKET_DATA_IDLE_SLEEP_SECONDS,
    MOCK_PRICE_BASE,
    MOCK_PRICE_SLEEP_SECONDS,
    MOCK_PRICE_VARIATION_MODULO,
)
from ibkr_trader.events import (
    DiagnosticEvent,
    EventBus,
    EventTopic,
    ExecutionEvent,
    OrderStatusEvent,
)
from ibkr_trader.market_data import MarketDataService, SubscriptionRequest
from ibkr_trader.models import OrderRequest, OrderSide, OrderType, SymbolContract
from ibkr_trader.portfolio import PortfolioState, RiskGuard
from ibkr_trader.presets import get_preset, preset_names
from ibkr_trader.safety import LiveTradingGuard
from ibkr_trader.sim.broker import SimulatedBroker, SimulatedMarketData
from ibkr_trader.strategy import (
    IndustryModelConfig,
    IndustryModelStrategy,
    SimpleMovingAverageStrategy,
    SMAConfig,
)
from ibkr_trader.strategy_adapters import ConfigBasedLiveStrategy
from ibkr_trader.strategy_configs.config import load_strategy_config
from ibkr_trader.strategy_configs.factory import StrategyFactory
from ibkr_trader.summary import summarize_run
from ibkr_trader.telemetry import TelemetryReporter, build_telemetry_reporter
from model.data import (
    FileCacheStore,
    IBKRMarketDataSource,
    IBKROptionChainSource,
    MarketDataClient,
    OptionChainCacheStore,
    OptionChainClient,
    OptionChainRequest,
    SnapshotLimitError,
    YFinanceMarketDataSource,
    YFinanceOptionChainSource,
)
from model.training.industry_model import train_linear_industry_model

app = typer.Typer(
    name="ibkr-trader",
    help="IBKR Personal Trading Platform - Paper trading by default",
)


def setup_logging(log_dir: Path, verbose: bool = False) -> None:
    """Configure loguru logging.

    Args:
        log_dir: Directory for log files
        verbose: Enable verbose debug logging
    """
    # Remove default handler
    logger.remove()

    # Console handler
    log_level = "DEBUG" if verbose else "INFO"
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan> - "
        "<level>{message}</level>",
        level=log_level,
    )

    # File handler
    logger.add(
        log_dir / "trader_{time}.log",
        rotation="1 day",
        retention="7 days",
        level="DEBUG",
    )


def create_market_data_client(
    source_name: str,
    cache_dir: Path,
    config: "IBKRConfig",  # noqa: F821
    *,
    max_snapshots: int,
    snapshot_interval: float,
    client_id: int,
    telemetry: TelemetryReporter | None = None,
) -> tuple[MarketDataClient, object]:
    """Instantiate a market data client for training workflows."""

    cache_dir.mkdir(parents=True, exist_ok=True)
    warning = telemetry.warning if telemetry is not None else None
    cache = FileCacheStore(
        cache_dir,
        ttl_seconds=config.training_price_cache_ttl,
        warning_handler=warning,
    )
    normalized = source_name.strip().lower()

    if normalized == "yfinance":
        source = YFinanceMarketDataSource()
    elif normalized == "ibkr":
        source = IBKRMarketDataSource(
            host=config.host,
            port=config.port,
            client_id=client_id,
            max_snapshots_per_session=max_snapshots,
            min_request_interval_seconds=snapshot_interval,
            warning_handler=warning,
        )
    else:
        raise typer.BadParameter(
            "Unsupported data source. Choose 'yfinance' or 'ibkr'.",
            param_hint="--data-source",
        )

    client = MarketDataClient(source=source, cache=cache)
    return client, source


def create_option_chain_client(
    source_name: str,
    cache_dir: Path,
    config: "IBKRConfig",  # noqa: F821
    *,
    max_snapshots: int,
    snapshot_interval: float,
    client_id: int,
    telemetry: TelemetryReporter | None = None,
) -> tuple[OptionChainClient, object]:
    """Instantiate an option chain client for caching workflows."""

    cache_dir.mkdir(parents=True, exist_ok=True)
    warning = telemetry.warning if telemetry is not None else None
    cache = OptionChainCacheStore(
        cache_dir,
        max_age_seconds=config.training_option_cache_ttl,
        warning_handler=warning,
    )
    normalized = source_name.strip().lower()

    if normalized == "yfinance":
        source = YFinanceOptionChainSource()
    elif normalized == "ibkr":
        source = IBKROptionChainSource(
            host=config.host,
            port=config.port,
            client_id=client_id,
            max_snapshots_per_session=max_snapshots,
            min_request_interval_seconds=snapshot_interval,
            warning_handler=warning,
        )
    else:
        raise typer.BadParameter(
            "Unsupported data source. Choose 'yfinance' or 'ibkr'.",
            param_hint="--data-source",
        )

    client = OptionChainClient(source=source, cache=cache)
    return client, source


def _format_seconds(value: float | None) -> str:
    if value is None:
        return "disabled"
    if value < 1:
        return f"{value * 1_000:.0f} ms"
    if value < 60:
        return f"{value:.0f} s"
    minutes, seconds = divmod(int(value), 60)
    hours, minutes = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds:
        parts.append(f"{seconds}s")
    return " ".join(parts) or "0s"


def _format_telemetry_line(line: str) -> str:
    payload = line.strip()
    if not payload:
        return ""
    try:
        record = json.loads(payload)
    except json.JSONDecodeError:
        return payload

    timestamp = record.get("timestamp", "")
    level = record.get("level", "")
    message = record.get("message", "")
    context = record.get("context")
    if context:
        context_blob = json.dumps(context, separators=(",", ":"), ensure_ascii=False)
        return f"{timestamp} {level}: {message} {context_blob}"
    return f"{timestamp} {level}: {message}"


def _load_portfolio_snapshot(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # pragma: no cover - defensive
        return None


def _tail_telemetry_entries(telemetry_file: Path, tail: int) -> list[str]:
    if not telemetry_file.exists():
        return []
    with telemetry_file.open("r", encoding="utf-8") as handle:
        lines = list(deque(handle, maxlen=tail if tail > 0 else None))
    formatted: list[str] = []
    for line in lines:
        formatted_line = _format_telemetry_line(line)
        if formatted_line:
            formatted.append(formatted_line)
    return formatted


def _emit_run_summary(
    *,
    config: "IBKRConfig",  # noqa: F821
    telemetry: TelemetryReporter,
    label: str,
    tail: int = 100,
) -> None:
    snapshot_path = config.data_dir / DEFAULT_PORTFOLIO_SNAPSHOT.name
    telemetry_file = config.log_dir / "telemetry.jsonl"
    summary = summarize_run(snapshot_path, _tail_telemetry_entries(telemetry_file, tail))

    logger.info("Session summary ({}): {}", label, summary.headline())

    if summary.telemetry_warnings:
        warning_lines = "\n".join(f"  {line}" for line in summary.telemetry_warnings)
        logger.warning("Recent telemetry warnings ({}):\n{}", label, warning_lines)

    telemetry.info(
        "run_summary",
        context={
            "label": label,
            "headline": summary.headline(),
            "telemetry_warnings": summary.telemetry_warnings,
            "recommended_actions": summary.recommended_actions,
            "trade_stats": summary.trade_stats,
        },
    )

    if summary.recommended_actions:
        logger.info("Recommended actions (%s):", label)
        for action in summary.recommended_actions:
            logger.info("  - %s", action)
    if summary.trade_stats:
        logger.info("Trade statistics (%s): %s", label, summary.trade_stats)


@app.command()
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
        f"Price cache directory: {price_cache_dir} (ttl={_format_seconds(price_cache.ttl_seconds)})"
    )
    typer.echo(
        "Option chain cache directory: "
        f"{option_cache_dir} (ttl={_format_seconds(option_cache.max_age_seconds)})"
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
                    _format_seconds(entry["age_seconds"])
                    if entry.get("age_seconds") is not None
                    else "n/a"
                )
                schema = entry.get("schema_version")
                typer.echo(
                    f"  {entry['symbol']} {entry['expiry']} | age={age_str} | schema={schema}"
                )


@app.command("session-status")
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

    summary = summarize_run(snapshot_path, _tail_telemetry_entries(telemetry_file, tail or 100))
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


@app.command("monitor-telemetry")
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
        entries = _tail_telemetry_entries(telemetry_file, tail)
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
                formatted = _format_telemetry_line(line)
                if formatted:
                    typer.echo(formatted)
    except KeyboardInterrupt:  # pragma: no cover - user initiated
        typer.echo("Stopping telemetry monitor.")


@app.command()
def run(
    symbols: list[str] = typer.Option(
        ["AAPL", "MSFT"],
        "--symbol",
        "-s",
        help="Symbols to trade (can specify multiple)",
    ),
    live: bool = typer.Option(
        False,
        "--live",
        help="Enable live trading (real money at risk)",
    ),
    fast_period: int = typer.Option(
        10,
        "--fast",
        "-f",
        help="Fast SMA period",
    ),
    slow_period: int = typer.Option(
        20,
        "--slow",
        "-w",
        help="Slow SMA period",
    ),
    position_size: int = typer.Option(
        10,
        "--size",
        "-p",
        help="Position size per trade",
    ),
    config_path: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        exists=True,
        readable=True,
        resolve_path=True,
        help="Path to strategy config JSON (overrides default SMA parameters)",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose debug logging",
    ),
) -> None:
    """Run the trading strategy.

    By default, runs in PAPER TRADING mode (no real money at risk).

    Supports two modes:
    1. Default SMA strategy with command-line parameters
    2. Config-based strategy loading with --config (experimental)

    To enable live trading, you must:
    1. Set IBKR_TRADING_MODE=live environment variable
    2. Pass --live flag
    3. Acknowledge the risk when prompted
    """
    # Load config
    config = load_config()
    setup_logging(config.log_dir, verbose)

    # Display mode prominently
    logger.info("=" * 70)
    logger.info("IBKR PERSONAL TRADER")
    logger.info(f"Mode: {config.trading_mode.value.upper()}")
    logger.info(f"Port: {config.port}")
    logger.info(f"Symbols: {', '.join(symbols)}")
    logger.info("=" * 70)
    logger.info(
        "Cache TTLs -> price=%s option=%s",
        _format_seconds(config.training_price_cache_ttl),
        _format_seconds(config.training_option_cache_ttl),
    )
    logger.info(
        "IBKR snapshot limits -> max=%d interval=%.2fs",
        config.training_max_snapshots,
        config.training_snapshot_interval,
    )

    # Initialize safety guard
    guard = LiveTradingGuard(config=config, live_flag_enabled=live)

    # Validate trading mode
    try:
        guard.validate_trading_mode()
    except Exception as e:
        logger.error(f"Trading mode validation failed: {e}")
        raise typer.Exit(code=1) from None

    # Prompt for live trading acknowledgment if needed
    if config.trading_mode == TradingMode.LIVE and live:
        logger.warning("=" * 70)
        logger.warning("LIVE TRADING MODE DETECTED")
        logger.warning("You are about to trade with real money")
        logger.warning("This can result in real financial loss")
        logger.warning("=" * 70)

        confirm = typer.confirm(
            "Do you acknowledge the risks and want to proceed with LIVE trading?"
        )

        if not confirm:
            logger.info("Live trading cancelled by user")
            raise typer.Exit()

        guard.acknowledge_live_trading()
    else:
        # Paper trading - safe to proceed
        guard.acknowledge_live_trading()

    # Run the strategy
    asyncio.run(
        run_strategy(
            config=config,
            guard=guard,
            symbols=symbols,
            fast_period=fast_period,
            slow_period=slow_period,
            position_size=position_size,
            config_path=config_path,
        )
    )


async def run_strategy(
    config: "IBKRConfig",  # noqa: F821
    guard: LiveTradingGuard,
    symbols: list[str],
    fast_period: int,
    slow_period: int,
    position_size: int,
    config_path: Path | None = None,
) -> None:
    """Run the trading strategy asynchronously.

    Args:
        config: IBKR configuration
        guard: Trading safety guard
        symbols: List of symbols to trade
        fast_period: Fast SMA period
        slow_period: Slow SMA period
        position_size: Position size per trade
        config_path: Optional path to strategy configuration JSON
    """
    # Initialize broker
    event_bus = EventBus()
    telemetry = build_telemetry_reporter(
        event_bus=event_bus,
        file_path=config.log_dir / "telemetry.jsonl",
    )
    telemetry.info(
        "Telemetry configured for strategy run",
        context={
            "symbols": symbols,
            "price_cache_ttl": config.training_price_cache_ttl,
            "option_cache_ttl": config.training_option_cache_ttl,
            "config_based": config_path is not None,
        },
    )
    market_data = MarketDataService(event_bus=event_bus)
    snapshot_path = config.data_dir / DEFAULT_PORTFOLIO_SNAPSHOT.name
    portfolio = PortfolioState(
        Decimal(str(config.max_daily_loss)),
        snapshot_path=snapshot_path,
    )
    risk_guard = RiskGuard(
        portfolio=portfolio,
        max_exposure=Decimal(str(config.max_order_exposure)),
    )
    broker = IBKRBroker(
        config=config,
        guard=guard,
        event_bus=event_bus,
        risk_guard=risk_guard,
    )
    strategy: SimpleMovingAverageStrategy | ConfigBasedLiveStrategy | None = None
    order_task: asyncio.Task[None] | None = None
    execution_task: asyncio.Task[None] | None = None
    diagnostic_task: asyncio.Task[None] | None = None
    stream_contexts: list[AbstractAsyncContextManager[None]] = []
    run_label = f"{config.trading_mode.value}-run"

    try:
        # Connect to IBKR
        await broker.connect()

        # Display account info
        account_summary = await broker.get_account_summary()
        logger.info(
            f"Account: {account_summary.get('AccountType', 'N/A')} - "
            f"Net Liquidation: ${float(account_summary.get('NetLiquidation', 0)):,.2f}"
        )
        await portfolio.update_account(account_summary)
        positions = await broker.get_positions()
        await portfolio.update_positions(positions)
        await portfolio.persist()

        if not config.use_mock_market_data:
            market_data.attach_ib(broker.ib)
            # Determine which symbols to subscribe to
            subscribe_symbols = symbols
            if config_path is not None:
                strat_config = load_strategy_config(config_path)
                subscribe_symbols = [strat_config.symbol]

            for symbol in subscribe_symbols:
                request = SubscriptionRequest(SymbolContract(symbol=symbol))
                context = market_data.subscribe(request)
                await context.__aenter__()
                stream_contexts.append(context)

        # Initialize strategy based on config or default SMA
        if config_path is not None:
            # Config-based strategy loading
            strat_config = load_strategy_config(config_path)
            logger.info(
                f"Loaded strategy config: {strat_config.name} ({strat_config.strategy_type})"
            )

            # Create replay strategy instance
            replay_strategy = StrategyFactory.create(strat_config)

            # Wrap in live adapter
            strategy = ConfigBasedLiveStrategy(
                impl=replay_strategy,
                broker=broker,
                event_bus=event_bus,
                symbol=strat_config.symbol,
            )
            logger.info(
                f"Config-based strategy initialized: {strat_config.name} "
                f"(type={strat_config.strategy_type}, symbol={strat_config.symbol})"
            )
        else:
            # Default SMA strategy
            strategy_config = SMAConfig(
                symbols=symbols,
                fast_period=fast_period,
                slow_period=slow_period,
                position_size=position_size,
            )
            strategy = SimpleMovingAverageStrategy(
                config=strategy_config,
                broker=broker,
                event_bus=event_bus,
                risk_guard=risk_guard,
            )
            logger.info(
                f"SMA strategy initialized: fast={fast_period}, slow={slow_period}, "
                f"size={position_size}"
            )

        await strategy.start()

        async def order_status_listener() -> None:
            subscription = event_bus.subscribe(EventTopic.ORDER_STATUS)
            try:
                async for event in subscription:
                    if isinstance(event, OrderStatusEvent):
                        await risk_guard.handle_order_status(event)
                        await portfolio.persist()
            except asyncio.CancelledError:
                raise

        order_task = asyncio.create_task(order_status_listener())

        async def execution_listener() -> None:
            subscription = event_bus.subscribe(EventTopic.EXECUTION)
            try:
                async for event in subscription:
                    if isinstance(event, ExecutionEvent):
                        await portfolio.record_execution_event(event)
                        await portfolio.persist()
            except asyncio.CancelledError:
                raise

        execution_task = asyncio.create_task(execution_listener())

        async def diagnostic_listener() -> None:
            subscription = event_bus.subscribe(EventTopic.DIAGNOSTIC)
            try:
                async for event in subscription:
                    if isinstance(event, DiagnosticEvent):
                        logger.log(
                            event.level,
                            "[diagnostic] %s %s",
                            event.message,
                            event.context or "",
                        )
            except asyncio.CancelledError:
                raise

        diagnostic_task = asyncio.create_task(diagnostic_listener())

        logger.info("Strategy running - monitoring market data...")
        logger.info("Press Ctrl+C to stop")

        if config.use_mock_market_data:
            counter = 0
            while True:
                counter += 1

                event_time = datetime.now(UTC)
                for symbol in symbols:
                    mock_price = MOCK_PRICE_BASE + Decimal(counter % MOCK_PRICE_VARIATION_MODULO)
                    await market_data.publish_price(symbol, mock_price, timestamp=event_time)

                await asyncio.sleep(MOCK_PRICE_SLEEP_SECONDS)
        else:
            while True:
                await asyncio.sleep(MARKET_DATA_IDLE_SLEEP_SECONDS)

    except KeyboardInterrupt:
        logger.info("Shutting down gracefully...")
    except Exception as e:
        logger.error(f"Error during execution: {e}")
        raise
    finally:
        if strategy is not None:
            await strategy.stop()
        if order_task is not None:
            order_task.cancel()
            with suppress(asyncio.CancelledError):
                await order_task
        if execution_task is not None:
            execution_task.cancel()
            with suppress(asyncio.CancelledError):
                await execution_task
        if diagnostic_task is not None:
            diagnostic_task.cancel()
            with suppress(asyncio.CancelledError):
                await diagnostic_task
        await broker.disconnect()
        _emit_run_summary(config=config, telemetry=telemetry, label=run_label)
        logger.info("Strategy stopped")


async def submit_single_order(
    config: "IBKRConfig",  # noqa: F821
    guard: LiveTradingGuard,
    contract: SymbolContract,
    side: OrderSide,
    quantity: int,
    order_type: OrderType,
    limit_price: Decimal | None,
    stop_price: Decimal | None,
    preview: bool,
    risk_guard: RiskGuard,
) -> None:
    """Submit a single order for connectivity testing."""
    broker = IBKRBroker(config=config, guard=guard, risk_guard=risk_guard)

    try:
        await broker.connect()

        order_request = OrderRequest(
            contract=contract,
            side=side,
            quantity=quantity,
            order_type=order_type,
            limit_price=limit_price,
            stop_price=stop_price,
            expected_price=limit_price or stop_price,
        )

        if preview:
            order_state = await broker.preview_order(order_request)
            logger.info(
                "Preview complete - Commission={commission}, InitMargin={init}, "
                "MaintenanceMargin={maint}",
                commission=getattr(order_state, "commission", "N/A"),
                init=order_state.initMarginChange,
                maint=order_state.maintMarginChange,
            )
        else:
            result = await broker.place_order(order_request)
            logger.info(
                "Test order submitted successfully: "
                f"order_id={result.order_id}, status={result.status.value}"
            )
    finally:
        await broker.disconnect()


@app.command()
def status() -> None:
    """Check connection status and display account information."""
    config = load_config()
    setup_logging(config.log_dir, verbose=False)

    asyncio.run(check_status(config))


async def check_status(config: "IBKRConfig") -> None:  # noqa: F821
    """Check status asynchronously."""
    guard = LiveTradingGuard(config=config, live_flag_enabled=False)
    broker = IBKRBroker(config=config, guard=guard)

    try:
        await broker.connect()

        # Get account summary
        summary = await broker.get_account_summary()

        logger.info("=" * 70)
        logger.info("ACCOUNT STATUS")
        logger.info("=" * 70)
        logger.info(f"Account Type: {summary.get('AccountType', 'N/A')}")
        logger.info(f"Net Liquidation: ${float(summary.get('NetLiquidation', 0)):,.2f}")
        logger.info(f"Total Cash: ${float(summary.get('TotalCashValue', 0)):,.2f}")
        logger.info(f"Buying Power: ${float(summary.get('BuyingPower', 0)):,.2f}")
        logger.info("=" * 70)

        # Get positions
        positions = await broker.get_positions()
        if positions:
            logger.info("\nCURRENT POSITIONS:")
            for pos in positions:
                logger.info(
                    f"  {pos.contract.symbol}: {pos.quantity} shares @ "
                    f"${pos.avg_cost:.2f} | P&L: ${pos.unrealized_pnl:.2f}"
                )
        else:
            logger.info("\nNo open positions")

    except Exception as e:
        logger.error(f"Failed to connect: {e}")
        raise typer.Exit(code=1) from None
    finally:
        await broker.disconnect()


@app.command()
def paper_order(
    symbol: str = typer.Option(..., "--symbol", "-s", help="Symbol to trade"),
    side: OrderSide = typer.Option(
        OrderSide.BUY,
        "--side",
        "-d",
        help="Order side",
    ),
    quantity: int = typer.Option(
        1,
        "--quantity",
        "-q",
        min=1,
        help="Order quantity",
    ),
    order_type: OrderType = typer.Option(
        OrderType.MARKET,
        "--type",
        "-t",
        help="Order type (MARKET/LIMIT/STOP/STOP_LIMIT)",
    ),
    sec_type: str = typer.Option(
        "STK",
        "--sec-type",
        help="Security type (e.g. STK, FUT, CASH, OPT).",
    ),
    exchange: str = typer.Option(
        "SMART",
        "--exchange",
        help="Exchange or routing destination (e.g. SMART, IDEALPRO).",
    ),
    currency: str = typer.Option(
        "USD",
        "--currency",
        help="Contract currency (e.g. USD, EUR).",
    ),
    limit_price: str | None = typer.Option(
        None,
        "--limit",
        help="Limit price (required for LIMIT/STOP_LIMIT orders)",
    ),
    stop_price: str | None = typer.Option(
        None,
        "--stop",
        help="Stop price (required for STOP/STOP_LIMIT orders)",
    ),
    preview: bool = typer.Option(
        False,
        "--preview",
        help="Run IB what-if preview instead of transmitting the order.",
    ),
) -> None:
    """Submit a single paper-trading order for connectivity testing."""
    config = load_config()
    setup_logging(config.log_dir, verbose=False)

    if config.trading_mode != TradingMode.PAPER:
        logger.error(
            "paper-order command is restricted to PAPER trading mode. "
            "Set IBKR_TRADING_MODE=paper before running this command."
        )
        raise typer.Exit(code=1)

    if order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and limit_price is None:
        raise typer.BadParameter("Limit price is required for LIMIT or STOP_LIMIT orders.")

    if order_type in (OrderType.STOP, OrderType.STOP_LIMIT) and stop_price is None:
        raise typer.BadParameter("Stop price is required for STOP or STOP_LIMIT orders.")

    limit_decimal: Decimal | None = None
    if limit_price is not None:
        try:
            limit_decimal = Decimal(limit_price)
        except (InvalidOperation, ValueError) as exc:
            raise typer.BadParameter("Invalid limit price format.") from exc

    stop_decimal: Decimal | None = None
    if stop_price is not None:
        try:
            stop_decimal = Decimal(stop_price)
        except (InvalidOperation, ValueError) as exc:
            raise typer.BadParameter("Invalid stop price format.") from exc

    guard = LiveTradingGuard(config=config, live_flag_enabled=False)
    snapshot_path = config.data_dir / DEFAULT_PORTFOLIO_SNAPSHOT.name
    portfolio = PortfolioState(
        Decimal(str(config.max_daily_loss)),
        snapshot_path=snapshot_path,
    )
    risk_guard = RiskGuard(
        portfolio=portfolio,
        max_exposure=Decimal(str(config.max_order_exposure)),
    )
    guard.acknowledge_live_trading()

    try:
        asyncio.run(
            submit_single_order(
                config=config,
                guard=guard,
                contract=SymbolContract(
                    symbol=symbol,
                    sec_type=sec_type.upper(),
                    exchange=exchange.upper(),
                    currency=currency.upper(),
                ),
                side=side,
                quantity=quantity,
                order_type=order_type,
                limit_price=limit_decimal,
                stop_price=stop_decimal,
                preview=preview,
                risk_guard=risk_guard,
            )
        )
    except Exception as exc:  # pragma: no cover - surface detailed CLI error
        logger.error(f"Failed to submit order: {exc}")
        raise typer.Exit(code=1) from exc


@app.command("paper-quick")
def paper_quick(
    preset: str = typer.Argument(
        ...,
        help="Preset name for quick trade (use --list-presets to discover).",
    ),
    side: OrderSide = typer.Option(
        OrderSide.BUY,
        "--side",
        "-d",
        help="Order side",
    ),
    quantity: int | None = typer.Option(
        None,
        "--quantity",
        "-q",
        min=1,
        help="Override preset quantity (defaults to preset value).",
    ),
    preview: bool = typer.Option(
        False,
        "--preview",
        help="Run IB what-if preview instead of transmitting the order.",
    ),
    list_presets: bool = typer.Option(
        False,
        "--list-presets",
        help="Display available presets and exit.",
    ),
) -> None:
    """Execute a quick preset-based paper trade."""
    if list_presets:
        typer.echo("Available presets:")
        for name in preset_names():
            typer.echo(f"  - {name}")
        raise typer.Exit()

    config = load_config()
    setup_logging(config.log_dir, verbose=False)

    if config.trading_mode != TradingMode.PAPER:
        logger.error(
            "paper-quick command is restricted to PAPER trading mode. "
            "Set IBKR_TRADING_MODE=paper before running this command."
        )
        raise typer.Exit(code=1)

    try:
        preset_obj = get_preset(preset)
    except KeyError:
        available = ", ".join(preset_names())
        logger.error(f"Unknown preset '{preset}'. Available: {available}")
        raise typer.Exit(code=1) from None

    contract, effective_quantity = preset_obj.with_quantity(quantity)

    guard = LiveTradingGuard(config=config, live_flag_enabled=False)
    snapshot_path = config.data_dir / DEFAULT_PORTFOLIO_SNAPSHOT.name
    portfolio = PortfolioState(
        Decimal(str(config.max_daily_loss)),
        snapshot_path=snapshot_path,
    )
    risk_guard = RiskGuard(
        portfolio=portfolio,
        max_exposure=Decimal(str(config.max_order_exposure)),
    )
    guard.acknowledge_live_trading()

    try:
        asyncio.run(
            submit_single_order(
                config=config,
                guard=guard,
                contract=contract,
                side=side,
                quantity=effective_quantity,
                order_type=OrderType.MARKET,
                limit_price=None,
                stop_price=None,
                preview=preview,
                risk_guard=risk_guard,
            )
        )
    except Exception as exc:  # pragma: no cover - surface detailed CLI error
        logger.error(f"Failed to submit preset order: {exc}")
        raise typer.Exit(code=1) from exc


@app.command()
def backtest(
    data_path: Path = typer.Argument(..., exists=True, readable=True, resolve_path=True),
    symbol: str = typer.Option("AAPL", "--symbol", help="Symbol represented in the dataset"),
    timestamp_column: str = typer.Option("timestamp", help="Column containing ISO timestamps"),
    price_column: str = typer.Option("close", help="Column containing price data"),
    fast_period: int = typer.Option(10, "--fast", help="Fast SMA period"),
    slow_period: int = typer.Option(20, "--slow", help="Slow SMA period"),
    position_size: int = typer.Option(10, "--size", help="Position size per trade"),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logging"),
    strategy_name: str = typer.Option("sma", "--strategy", help="Backtest strategy (sma|industry)"),
    model_artifact: Path | None = typer.Option(
        None,
        "--model-artifact",
        exists=True,
        readable=True,
        help="Path to trained industry model artifact (required for industry strategy)",
    ),
    entry_threshold: float = typer.Option(
        0.0,
        "--entry-threshold",
        help="Minimum relative edge before trading (industry strategy)",
    ),
) -> None:
    """Run a backtest using historical price data from CSV."""
    config = load_config()
    setup_logging(config.log_dir, verbose)

    try:
        frame = pd.read_csv(data_path)
    except Exception as exc:  # pragma: no cover - IO error surfaces to user
        logger.error(f"Failed to load data: {exc}")
        raise typer.Exit(code=1) from exc

    if timestamp_column not in frame.columns or price_column not in frame.columns:
        logger.error(
            "Backtest dataset must contain '%s' and '%s' columns",
            timestamp_column,
            price_column,
        )
        raise typer.Exit(code=1)

    frame = frame[[timestamp_column, price_column]].dropna()
    if frame.empty:
        logger.error("Backtest dataset is empty after filtering")
        raise typer.Exit(code=1)

    frame[timestamp_column] = pd.to_datetime(frame[timestamp_column], utc=True, errors="raise")
    frame = frame.sort_values(timestamp_column)

    bars = [
        (row[timestamp_column].to_pydatetime(), Decimal(str(row[price_column])))
        for row in frame.itertuples(index=False)
    ]

    event_bus = EventBus()
    telemetry = build_telemetry_reporter(
        event_bus=event_bus,
        file_path=config.log_dir / "telemetry.jsonl",
    )
    market_data = SimulatedMarketData(event_bus)
    snapshot_path = config.data_dir / DEFAULT_PORTFOLIO_SNAPSHOT.name
    portfolio = PortfolioState(Decimal(str(config.max_daily_loss)), snapshot_path=snapshot_path)
    risk_guard = RiskGuard(
        portfolio=portfolio,
        max_exposure=Decimal(str(config.max_order_exposure)),
    )
    broker = SimulatedBroker(event_bus=event_bus, risk_guard=risk_guard)

    strategy_name_normalized = strategy_name.lower()
    if strategy_name_normalized not in {"sma", "industry"}:
        logger.error("Unsupported strategy '%s'", strategy_name)
        raise typer.Exit(code=1)

    if strategy_name_normalized == "industry":
        if model_artifact is None:
            logger.error("--model-artifact is required for industry strategy backtests")
            raise typer.Exit(code=1)
        industry_config = IndustryModelConfig(
            name="IndustryModel",
            symbols=[symbol],
            position_size=position_size,
            artifact_path=model_artifact,
            entry_threshold=Decimal(str(entry_threshold)),
        )
        strategy = IndustryModelStrategy(
            config=industry_config,
            broker=broker,
            event_bus=event_bus,
            risk_guard=risk_guard,
        )
    else:
        strategy_config = SMAConfig(
            symbols=[symbol],
            fast_period=fast_period,
            slow_period=slow_period,
            position_size=position_size,
        )
        strategy = SimpleMovingAverageStrategy(
            config=strategy_config,
            broker=broker,
            event_bus=event_bus,
            risk_guard=risk_guard,
        )

    engine = BacktestEngine(
        symbol=symbol,
        event_bus=event_bus,
        market_data=market_data,
        broker=broker,
        portfolio=portfolio,
        risk_guard=risk_guard,
    )

    asyncio.run(engine.run(strategy, bars))

    logger.info("Backtest completed with %s executions", len(broker.execution_events))
    _emit_run_summary(config=config, telemetry=telemetry, label="backtest")


@app.command("train-model")
def train_model_command(
    target_symbol: str = typer.Option(..., "--target", help="Target symbol to forecast"),
    peer_symbols: list[str] = typer.Option(
        ["MSFT", "GOOGL"],
        "--peer",
        "-p",
        help="Peer symbols (repeat for multiple)",
        show_default=True,
    ),
    start: datetime = typer.Option(
        ...,
        "--start",
        formats=["%Y-%m-%d"],
        help="Training window start date (YYYY-MM-DD)",
    ),
    end: datetime = typer.Option(
        ...,
        "--end",
        formats=["%Y-%m-%d"],
        help="Training window end date (YYYY-MM-DD)",
    ),
    horizon_days: int = typer.Option(5, "--horizon", help="Prediction horizon in trading days"),
    artifact_dir: Path = typer.Option(
        Path("model/artifacts/industry_forecast"),
        "--artifact-dir",
        resolve_path=True,
        help="Directory to store model artifacts",
    ),
    data_source: str | None = typer.Option(
        None,
        "--data-source",
        help=(
            "Market data source identifier (yfinance|ibkr). "
            "Defaults to config.training_data_source."
        ),
    ),
    cache_dir: Path | None = typer.Option(
        None,
        "--cache-dir",
        resolve_path=True,
        help=(
            "Local cache directory for downloaded market data. "
            "Defaults to config.training_cache_dir."
        ),
    ),
    max_snapshots: int | None = typer.Option(
        None,
        "--max-snapshots",
        help=(
            "Maximum historical data requests per session (IBKR only). "
            "Defaults to config.training_max_snapshots."
        ),
    ),
    snapshot_interval: float | None = typer.Option(
        None,
        "--snapshot-interval",
        help=(
            "Minimum seconds between IBKR historical requests. "
            "Defaults to config.training_snapshot_interval."
        ),
    ),
    ibkr_client_id: int | None = typer.Option(
        None,
        "--ibkr-client-id",
        help=(
            "Client ID to use for IBKR historical requests. Defaults to config.training_client_id."
        ),
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
) -> None:
    """Train the sample industry model using the configured data source."""

    if not peer_symbols:
        raise typer.BadParameter("At least one --peer symbol is required.", param_hint="--peer")

    if target_symbol in peer_symbols:
        raise typer.BadParameter(
            "Target symbol must not be present in the peer list.",
            param_hint="--peer",
        )

    config = load_config()
    setup_logging(config.log_dir, verbose)

    artifact_dir.mkdir(parents=True, exist_ok=True)

    telemetry = build_telemetry_reporter(
        file_path=config.log_dir / "telemetry.jsonl",
    )

    resolved_data_source = data_source or config.training_data_source
    resolved_cache_dir = cache_dir or config.training_cache_dir
    resolved_max_snapshots = (
        max_snapshots if max_snapshots is not None else config.training_max_snapshots
    )
    resolved_snapshot_interval = (
        snapshot_interval if snapshot_interval is not None else config.training_snapshot_interval
    )
    resolved_client_id = ibkr_client_id if ibkr_client_id is not None else config.training_client_id

    client: MarketDataClient | None = None
    source: object | None = None

    try:
        client, source = create_market_data_client(
            resolved_data_source,
            resolved_cache_dir,
            config,
            max_snapshots=resolved_max_snapshots,
            snapshot_interval=resolved_snapshot_interval,
            client_id=resolved_client_id,
            telemetry=telemetry,
        )

        artifact_path = train_linear_industry_model(
            target_symbol=target_symbol,
            peer_symbols=peer_symbols,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            horizon_days=horizon_days,
            artifact_dir=artifact_dir,
            data_client=client,
        )

    except SnapshotLimitError as exc:
        logger.error("Snapshot limit reached while requesting IBKR data: %s", exc)
        raise typer.Exit(code=1) from exc
    except typer.BadParameter:
        raise
    except Exception as exc:  # pragma: no cover - surfaces detailed error to user
        logger.error(f"Model training failed: {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        if hasattr(source, "close") and callable(source.close):
            with suppress(Exception):
                source.close()

    logger.info("Model artifact stored at %s", artifact_path)
    predictions_path = artifact_path.parent / f"{target_symbol}_predictions.csv"
    if predictions_path.exists():
        logger.info("Predictions saved to %s", predictions_path)
    else:  # pragma: no cover - defensive logging
        logger.warning("Prediction CSV not found at expected path: %s", predictions_path)


@app.command("cache-option-chain")
def cache_option_chain_command(
    symbol: str = typer.Option(..., "--symbol", help="Underlying symbol (e.g. AAPL)"),
    expiry: datetime = typer.Option(
        ...,
        "--expiry",
        formats=["%Y-%m-%d"],
        help="Option expiry date (YYYY-MM-DD)",
    ),
    data_source: str | None = typer.Option(
        None,
        "--data-source",
        help=(
            "Option data source identifier (yfinance|ibkr). "
            "Defaults to config.training_data_source."
        ),
    ),
    cache_dir: Path | None = typer.Option(
        None,
        "--cache-dir",
        resolve_path=True,
        help=(
            "Cache directory for option chains. "
            "Defaults to config.training_cache_dir/option_chains."
        ),
    ),
    max_snapshots: int | None = typer.Option(
        None,
        "--max-snapshots",
        help=(
            "Maximum IBKR option data requests per session. "
            "Defaults to config.training_max_snapshots."
        ),
    ),
    snapshot_interval: float | None = typer.Option(
        None,
        "--snapshot-interval",
        help=(
            "Minimum seconds between IBKR option requests. "
            "Defaults to config.training_snapshot_interval."
        ),
    ),
    ibkr_client_id: int | None = typer.Option(
        None,
        "--ibkr-client-id",
        help=("Client ID to use for IBKR option requests. Defaults to config.training_client_id."),
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
) -> None:
    """Fetch and cache an option chain using the configured data source."""

    config = load_config()
    setup_logging(config.log_dir, verbose)

    telemetry = build_telemetry_reporter(
        file_path=config.log_dir / "telemetry.jsonl",
    )

    resolved_data_source = data_source or config.training_data_source
    base_cache_dir = cache_dir or (config.training_cache_dir / "option_chains")
    resolved_max_snapshots = (
        max_snapshots if max_snapshots is not None else config.training_max_snapshots
    )
    resolved_snapshot_interval = (
        snapshot_interval if snapshot_interval is not None else config.training_snapshot_interval
    )
    resolved_client_id = ibkr_client_id if ibkr_client_id is not None else config.training_client_id

    client: OptionChainClient | None = None
    source: object | None = None

    try:
        client, source = create_option_chain_client(
            resolved_data_source,
            base_cache_dir,
            config,
            max_snapshots=resolved_max_snapshots,
            snapshot_interval=resolved_snapshot_interval,
            client_id=resolved_client_id,
            telemetry=telemetry,
        )

        chain = client.get_option_chain(
            OptionChainRequest(symbol=symbol, expiry=expiry.replace(tzinfo=UTC))
        )
    except SnapshotLimitError as exc:
        logger.error("Snapshot limit reached while requesting IBKR options: %s", exc)
        raise typer.Exit(code=1) from exc
    except Exception as exc:  # pragma: no cover - surface errors to user
        logger.error(f"Failed to cache option chain: {exc}")
        raise typer.Exit(code=1) from exc
    finally:
        if hasattr(source, "close") and callable(source.close):
            with suppress(Exception):
                source.close()

    logger.info(
        "Cached option chain for %s expiring %s (%d calls, %d puts)",
        symbol.upper(),
        expiry.strftime("%Y-%m-%d"),
        len(chain.calls),
        len(chain.puts),
    )


@app.command()
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
    snapshot_path = config.data_dir / DEFAULT_PORTFOLIO_SNAPSHOT.name
    portfolio = PortfolioState(
        Decimal(str(config.max_daily_loss)),
        snapshot_path=snapshot_path,
    )
    risk_guard = RiskGuard(
        portfolio=portfolio,
        max_exposure=Decimal(str(config.max_order_exposure)),
    )
    guard = LiveTradingGuard(config=config, live_flag_enabled=False)
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


if __name__ == "__main__":
    app()
