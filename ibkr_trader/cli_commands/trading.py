"""Trading commands for IBKR Personal Trader."""

import asyncio
from contextlib import AbstractAsyncContextManager, suppress
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import typer
from loguru import logger

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
from ibkr_trader.models import (
    BracketOrderRequest,
    OrderRequest,
    OrderSide,
    OrderType,
    SymbolContract,
)
from ibkr_trader.portfolio import PortfolioState, RiskGuard
from ibkr_trader.presets import get_preset, preset_names
from ibkr_trader.safety import LiveTradingGuard
from ibkr_trader.strategy import SimpleMovingAverageStrategy, SMAConfig
from ibkr_trader.strategy_adapters import ConfigBasedLiveStrategy
from ibkr_trader.strategy_configs.config import load_strategy_config
from ibkr_trader.strategy_configs.factory import StrategyFactory
from ibkr_trader.telemetry import build_telemetry_reporter

trading_app = typer.Typer()


async def _emit_shutdown_summary(
    config: "IBKRConfig",  # noqa: F821
    portfolio: PortfolioState,
    broker: IBKRBroker,
    run_label: str,
) -> None:
    """Display final summary on shutdown with position warnings."""
    logger.info("")
    logger.info("=" * 70)
    logger.info("FINAL SESSION SUMMARY")
    logger.info("=" * 70)

    # Get final positions
    try:
        positions = await broker.get_positions()
        await portfolio.update_positions(positions)
    except Exception as e:  # pragma: no cover - best effort
        logger.warning(f"Could not fetch final positions: {e}")
        positions = []

    # Display P&L
    realized_pnl = await portfolio.realized_pnl()
    per_symbol_pnl = await portfolio.per_symbol_pnl()
    trade_stats = await portfolio.trade_statistics()

    logger.info(f"Realized P&L: ${realized_pnl}")
    logger.info(f"Total Fills: {trade_stats.get('fills', '0')}")

    if per_symbol_pnl:
        logger.info("")
        logger.info("Per-Symbol P&L:")
        for symbol, pnl in per_symbol_pnl.items():
            logger.info(f"  {symbol}: ${pnl}")

    # Check for open positions
    if positions:
        logger.warning("")
        logger.warning("⚠ OPEN POSITIONS DETECTED ⚠")
        logger.warning("")
        logger.warning("You have %d open position(s):", len(positions))
        for pos in positions:
            qty = pos.quantity
            symbol = pos.contract.symbol
            avg_price = pos.avg_price
            unrealized = pos.unrealized_pnl
            pnl_sign = "+" if unrealized >= 0 else ""
            logger.warning(
                f"  {symbol}: {qty:+d} shares @ ${avg_price:.2f} | "
                f"Unrealized P&L: {pnl_sign}${unrealized:.2f}"
            )
        logger.warning("")
        logger.warning("Remember to close these positions if needed!")
    else:
        logger.info("")
        logger.info("✓ All positions closed")

    logger.info("=" * 70)
    logger.info("")


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


@trading_app.command()
def status() -> None:
    """Check connection status and display account information."""
    from ibkr_trader.cli_commands.utils import setup_logging

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


@trading_app.command()
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
    from ibkr_trader.cli_commands.utils import setup_logging

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


@trading_app.command("paper-quick")
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
    from ibkr_trader.cli_commands.utils import setup_logging

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


@trading_app.command("bracket-order")
def bracket_order(
    symbol: str = typer.Option(..., "--symbol", "-s", help="Symbol to trade"),
    side: OrderSide = typer.Option(
        OrderSide.BUY,
        "--side",
        "-d",
        help="Order side (BUY or SELL)",
    ),
    quantity: int = typer.Option(
        1,
        "--quantity",
        "-q",
        min=1,
        help="Order quantity",
    ),
    entry_type: OrderType = typer.Option(
        OrderType.MARKET,
        "--entry-type",
        help="Entry order type (MARKET or LIMIT)",
    ),
    entry_limit: str | None = typer.Option(
        None,
        "--entry-limit",
        help="Entry limit price (required for LIMIT entry)",
    ),
    stop_loss: str = typer.Option(
        ...,
        "--stop-loss",
        help="Stop loss price",
    ),
    take_profit: str = typer.Option(
        ...,
        "--take-profit",
        help="Take profit price",
    ),
    preview: bool = typer.Option(
        False,
        "--preview",
        help="Preview order without submitting",
    ),
) -> None:
    """Submit a bracket order (entry + stop loss + take profit) for paper trading.

    A bracket order consists of three linked orders:
    - Parent (entry): Initial order to enter position
    - Stop loss: Automatic exit if price moves against you
    - Take profit: Automatic exit to lock in profits

    When the entry fills, both stop loss and take profit orders activate.
    If either child fills, the other is automatically cancelled (OCO).

    Example (long position):
      ibkr-trader bracket-order --symbol AAPL --side BUY --quantity 10 \\
        --stop-loss 145.00 --take-profit 155.00

    Example (short position):
      ibkr-trader bracket-order --symbol AAPL --side SELL --quantity 10 \\
        --stop-loss 155.00 --take-profit 145.00
    """
    from ibkr_trader.cli_commands.utils import setup_logging

    config = load_config()
    setup_logging(config.log_dir, verbose=False)

    if config.trading_mode != TradingMode.PAPER:
        logger.error(
            "bracket-order command is restricted to PAPER trading mode. "
            "Set IBKR_TRADING_MODE=paper before running this command."
        )
        raise typer.Exit(code=1)

    # Parse prices
    try:
        stop_loss_decimal = Decimal(stop_loss)
        take_profit_decimal = Decimal(take_profit)
    except (InvalidOperation, ValueError) as exc:
        raise typer.BadParameter("Invalid price format for stop-loss or take-profit.") from exc

    entry_limit_decimal: Decimal | None = None
    if entry_type == OrderType.LIMIT:
        if entry_limit is None:
            raise typer.BadParameter("--entry-limit is required for LIMIT entry orders.")
        try:
            entry_limit_decimal = Decimal(entry_limit)
        except (InvalidOperation, ValueError) as exc:
            raise typer.BadParameter("Invalid entry limit price format.") from exc

    # Create broker components
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
            submit_bracket_order(
                config=config,
                guard=guard,
                risk_guard=risk_guard,
                symbol=symbol,
                side=side,
                quantity=quantity,
                entry_type=entry_type,
                entry_limit_price=entry_limit_decimal,
                stop_loss_price=stop_loss_decimal,
                take_profit_price=take_profit_decimal,
                preview=preview,
            )
        )
    except Exception as exc:  # pragma: no cover - surface detailed CLI error
        logger.error(f"Failed to submit bracket order: {exc}")
        raise typer.Exit(code=1) from exc


async def submit_bracket_order(
    config: "IBKRConfig",  # noqa: F821
    guard: LiveTradingGuard,
    risk_guard: RiskGuard,
    symbol: str,
    side: OrderSide,
    quantity: int,
    entry_type: OrderType,
    entry_limit_price: Decimal | None,
    stop_loss_price: Decimal,
    take_profit_price: Decimal,
    preview: bool,
) -> None:
    """Submit a bracket order for connectivity testing."""
    broker = IBKRBroker(config=config, guard=guard, risk_guard=risk_guard)

    try:
        await broker.connect()

        # Determine child order sides (opposite of parent)
        child_side = OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY

        # Create parent order
        parent = OrderRequest(
            contract=SymbolContract(symbol=symbol),
            side=side,
            quantity=quantity,
            order_type=entry_type,
            limit_price=entry_limit_price,
            expected_price=entry_limit_price,
        )

        # Create stop loss order
        stop_loss = OrderRequest(
            contract=SymbolContract(symbol=symbol),
            side=child_side,
            quantity=quantity,
            order_type=OrderType.STOP,
            stop_price=stop_loss_price,
            expected_price=stop_loss_price,
        )

        # Create take profit order
        take_profit = OrderRequest(
            contract=SymbolContract(symbol=symbol),
            side=child_side,
            quantity=quantity,
            order_type=OrderType.LIMIT,
            limit_price=take_profit_price,
            expected_price=take_profit_price,
        )

        bracket_request = BracketOrderRequest(
            parent=parent,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

        if preview:
            # Preview parent order only (IBKR doesn't support bracket preview directly)
            order_state = await broker.preview_order(parent)
            logger.info(
                "Bracket order preview - Commission={commission}, InitMargin={init}, "
                "MaintenanceMargin={maint}",
                commission=getattr(order_state, "commission", "N/A"),
                init=order_state.initMarginChange,
                maint=order_state.maintMarginChange,
            )
            logger.info(
                "Bracket structure: entry={entry} qty={qty}, stop_loss={stop}, take_profit={tp}",
                entry=symbol,
                qty=quantity,
                stop=stop_loss_price,
                tp=take_profit_price,
            )
        else:
            result = await broker.place_bracket_order(bracket_request)
            logger.info(
                "Bracket order submitted successfully: "
                f"parent_order_id={result.order_id}, "
                f"child_order_ids={result.child_order_ids}, "
                f"status={result.status.value}"
            )
            logger.info(
                "Entry: {side} {qty} {symbol} @ {entry_type}",
                side=side.value,
                qty=quantity,
                symbol=symbol,
                entry_type=entry_type.value,
            )
            logger.info(f"Stop Loss: {stop_loss_price}")
            logger.info(f"Take Profit: {take_profit_price}")
    finally:
        await broker.disconnect()


@trading_app.command()
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
    from ibkr_trader.cli_commands.utils import format_seconds, setup_logging

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
        format_seconds(config.training_price_cache_ttl),
        format_seconds(config.training_option_cache_ttl),
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
    from ibkr_trader.cli_commands.utils import emit_run_summary

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
        logger.info("=" * 70)
        logger.warning("SHUTDOWN INITIATED BY USER")
        logger.info("=" * 70)
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

        # Display final summary before disconnecting
        await _emit_shutdown_summary(
            config=config,
            portfolio=portfolio,
            broker=broker,
            run_label=run_label,
        )

        await broker.disconnect()
        emit_run_summary(config=config, telemetry=telemetry, label=run_label)
        logger.info("Strategy stopped")
