"""CLI entry point for IBKR Personal Trader."""

import asyncio
import sys
from contextlib import AbstractAsyncContextManager, suppress
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
from ibkr_trader.events import EventBus, EventTopic, OrderStatusEvent
from ibkr_trader.market_data import MarketDataService, SubscriptionRequest
from ibkr_trader.models import OrderRequest, OrderSide, OrderType, SymbolContract
from ibkr_trader.portfolio import PortfolioState, RiskGuard
from ibkr_trader.presets import get_preset, preset_names
from ibkr_trader.safety import LiveTradingGuard
from ibkr_trader.strategy import SimpleMovingAverageStrategy, SMAConfig

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
        help="⚠️  ENABLE LIVE TRADING (real money at risk) ⚠️",
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
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose debug logging",
    ),
) -> None:
    """Run the trading strategy.

    By default, runs in PAPER TRADING mode (no real money at risk).

    To enable live trading, you must:
    1. Set IBKR_TRADING_MODE=live environment variable
    2. Pass --live flag
    3. Acknowledge the risk when prompted
    """
    # Load config
    config = load_config()
    setup_logging(config.log_dir, verbose)

    # Display mode prominently
    mode_symbol = "📄" if config.trading_mode == TradingMode.PAPER else "⚠️"
    logger.info("=" * 70)
    logger.info(f"{mode_symbol}  IBKR PERSONAL TRADER  {mode_symbol}")
    logger.info(f"Mode: {config.trading_mode.value.upper()}")
    logger.info(f"Port: {config.port}")
    logger.info(f"Symbols: {', '.join(symbols)}")
    logger.info("=" * 70)

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
        logger.warning("⚠️  LIVE TRADING MODE DETECTED  ⚠️")
        logger.warning("You are about to trade with REAL MONEY")
        logger.warning("This can result in REAL FINANCIAL LOSS")
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
        )
    )


async def run_strategy(
    config: "IBKRConfig",  # noqa: F821
    guard: LiveTradingGuard,
    symbols: list[str],
    fast_period: int,
    slow_period: int,
    position_size: int,
) -> None:
    """Run the trading strategy asynchronously.

    Args:
        config: IBKR configuration
        guard: Trading safety guard
        symbols: List of symbols to trade
        fast_period: Fast SMA period
        slow_period: Slow SMA period
        position_size: Position size per trade
    """
    # Initialize broker
    event_bus = EventBus()
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
    strategy: SimpleMovingAverageStrategy | None = None
    order_task: asyncio.Task[None] | None = None
    stream_contexts: list[AbstractAsyncContextManager[None]] = []

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
            for symbol in symbols:
                request = SubscriptionRequest(SymbolContract(symbol=symbol))
                context = market_data.subscribe(request)
                await context.__aenter__()
                stream_contexts.append(context)

        # Initialize strategy
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

        logger.info(f"Strategy initialized: {strategy_config.name}")
        logger.info(f"Fast SMA: {fast_period}, Slow SMA: {slow_period}")
        logger.info("Starting price monitoring... (Press Ctrl+C to stop)")

        if config.use_mock_market_data:
            counter = 0
            while True:
                counter += 1

                for symbol in symbols:
                    mock_price = MOCK_PRICE_BASE + Decimal(counter % MOCK_PRICE_VARIATION_MODULO)
                    await market_data.publish_price(symbol, mock_price)

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
        await broker.disconnect()
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


if __name__ == "__main__":
    app()
