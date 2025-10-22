"""Trading commands for IBKR Personal Trader."""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager, suppress
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from difflib import get_close_matches
from pathlib import Path

import typer
from loguru import logger

from ibkr_trader.cli_commands.utils import (
    build_portfolio_and_risk_guard,
    build_telemetry_alert_router,
    load_kill_switch,
    load_symbol_limit_registry,
)
from ibkr_trader.config import TradingMode, load_config
from ibkr_trader.constants import (
    DEFAULT_SYMBOL_LIMITS_FILE,
    MARKET_DATA_IDLE_SLEEP_SECONDS,
    MOCK_PRICE_BASE,
    MOCK_PRICE_SLEEP_SECONDS,
    MOCK_PRICE_VARIATION_MODULO,
)
from ibkr_trader.core.alerting import AlertMessage
from ibkr_trader.data import LiquidityScreener, LiquidityScreenerConfig
from ibkr_trader.events import (
    DiagnosticEvent,
    EventBus,
    EventTopic,
    ExecutionEvent,
    OrderStatusEvent,
)
from ibkr_trader.execution import IBKRBroker
from ibkr_trader.market_data import MarketDataService, SubscriptionRequest
from ibkr_trader.models import (
    BracketOrderRequest,
    OCOOrderRequest,
    OrderRequest,
    OrderSide,
    OrderType,
    SymbolContract,
    TrailingStopConfig,
)
from ibkr_trader.portfolio import PortfolioState, RiskGuard
from ibkr_trader.presets import get_preset, preset_names
from ibkr_trader.safety import LiveTradingGuard
from ibkr_trader.strategies import AdaptiveMomentumConfig, AdaptiveMomentumStrategy
from ibkr_trader.strategy import SimpleMovingAverageStrategy, SMAConfig
from ibkr_trader.strategy_adapters import ConfigBasedLiveStrategy
from ibkr_trader.strategy_configs.config import load_strategy_config
from ibkr_trader.strategy_configs.factory import StrategyFactory
from ibkr_trader.strategy_configs.graph import StrategyGraphConfig, load_strategy_graph
from ibkr_trader.strategy_coordinator import StrategyCoordinator
from ibkr_trader.telemetry import build_telemetry_reporter

trading_app = typer.Typer()


async def _emit_shutdown_summary(
    config: IBKRConfig,  # noqa: F821
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
            avg_price = getattr(pos, "avg_price", getattr(pos, "avg_cost", Decimal("0")))
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
    config: IBKRConfig,  # noqa: F821
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


async def check_status(config: IBKRConfig) -> None:  # noqa: F821
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
    _, risk_guard, _ = build_portfolio_and_risk_guard(config)
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
        available_list = list(preset_names())
        lower_to_name = {name.lower(): name for name in available_list}
        suggestion_keys = get_close_matches(preset.lower(), lower_to_name.keys(), n=1)
        suggestion = lower_to_name[suggestion_keys[0]] if suggestion_keys else None
        suggestion_msg = f" Did you mean '{suggestion}'?" if suggestion else ""
        available = ", ".join(available_list)
        message = (
            f"Unknown preset '{preset}'. Available: {available}.{suggestion_msg} "
            "Use '--list-presets' to inspect details."
        )
        logger.error(message)
        raise typer.Exit(code=1) from None

    contract, effective_quantity = preset_obj.with_quantity(quantity)

    guard = LiveTradingGuard(config=config, live_flag_enabled=False)
    _, risk_guard, _ = build_portfolio_and_risk_guard(config)
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
    _, risk_guard, _ = build_portfolio_and_risk_guard(config)
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
    config: IBKRConfig,  # noqa: F821
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


@trading_app.command("trailing-stop")
def trailing_stop_command(
    symbol: str = typer.Option(..., "--symbol", "-s", help="Symbol to trail"),
    side: OrderSide = typer.Option(
        OrderSide.SELL,
        "--side",
        "-d",
        help="Stop order side (SELL for long position, BUY for short position)",
    ),
    quantity: int = typer.Option(
        1,
        "--quantity",
        "-q",
        min=1,
        help="Position quantity",
    ),
    trail_amount: str | None = typer.Option(
        None,
        "--trail-amount",
        help="Trailing amount in dollars (e.g., 5.00)",
    ),
    trail_percent: str | None = typer.Option(
        None,
        "--trail-percent",
        help="Trailing percentage (e.g., 2.0 for 2%)",
    ),
    activation_price: str | None = typer.Option(
        None,
        "--activation-price",
        help="Optional activation threshold (start trailing above this price)",
    ),
    initial_price: str = typer.Option(
        ...,
        "--initial-price",
        help="Current market price for calculating initial stop",
    ),
) -> None:
    """Create a trailing stop that adjusts dynamically as price moves favorably.

    A trailing stop automatically adjusts the stop loss price as the market moves
    in your favor. For long positions (SELL stop), the stop rises with price
    increases. For short positions (BUY stop), the stop lowers with price decreases.

    The stop never widens (moves against your position).

    You must specify EITHER --trail-amount OR --trail-percent (not both).

    Example (long position with $5 trailing amount):
      ibkr-trader trailing-stop --symbol AAPL --side SELL --quantity 10 \\
        --trail-amount 5.00 --initial-price 150.00

    Example (short position with 2% trailing):
      ibkr-trader trailing-stop --symbol AAPL --side BUY --quantity 10 \\
        --trail-percent 2.0 --initial-price 150.00 --activation-price 145.00

    NOTE: This command creates the trailing stop but does not monitor it.
    You must run 'ibkr-trader run' or integrate TrailingStopManager in your strategy
    to enable continuous monitoring and adjustment.
    """
    from ibkr_trader.cli_commands.utils import setup_logging

    config = load_config()
    setup_logging(config.log_dir, verbose=False)

    if config.trading_mode != TradingMode.PAPER:
        logger.error(
            "trailing-stop command is restricted to PAPER trading mode. "
            "Set IBKR_TRADING_MODE=paper before running this command."
        )
        raise typer.Exit(code=1)

    # Validate mutually exclusive parameters
    if trail_amount is None and trail_percent is None:
        raise typer.BadParameter("Must specify either --trail-amount or --trail-percent")
    if trail_amount is not None and trail_percent is not None:
        raise typer.BadParameter("Cannot specify both --trail-amount and --trail-percent")

    # Parse prices
    try:
        initial_price_decimal = Decimal(initial_price)
        trail_amount_decimal: Decimal | None = Decimal(trail_amount) if trail_amount else None
        trail_percent_decimal: Decimal | None = Decimal(trail_percent) if trail_percent else None
        activation_price_decimal: Decimal | None = (
            Decimal(activation_price) if activation_price else None
        )
    except (InvalidOperation, ValueError) as exc:
        raise typer.BadParameter("Invalid price or percentage format") from exc

    # Create trailing stop config
    try:
        config_obj = TrailingStopConfig(
            symbol=symbol,
            side=side,
            quantity=quantity,
            trail_amount=trail_amount_decimal,
            trail_percent=trail_percent_decimal,
            activation_price=activation_price_decimal,
        )
    except Exception as exc:
        raise typer.BadParameter(f"Invalid trailing stop configuration: {exc}") from exc

    # Create broker and trailing stop manager
    guard = LiveTradingGuard(config=config, live_flag_enabled=False)
    _, risk_guard, _ = build_portfolio_and_risk_guard(config)
    guard.acknowledge_live_trading()

    try:
        asyncio.run(
            create_trailing_stop(
                config=config,
                guard=guard,
                risk_guard=risk_guard,
                trailing_config=config_obj,
                initial_price=initial_price_decimal,
            )
        )
    except Exception as exc:  # pragma: no cover - surface detailed CLI error
        logger.error(f"Failed to create trailing stop: {exc}")
        raise typer.Exit(code=1) from exc


async def create_trailing_stop(
    config: IBKRConfig,  # noqa: F821
    guard: LiveTradingGuard,
    risk_guard: RiskGuard,
    trailing_config: TrailingStopConfig,
    initial_price: Decimal,
) -> None:
    """Create a trailing stop order."""
    from ibkr_trader.trailing_stops import TrailingStopManager

    event_bus = EventBus()
    broker = IBKRBroker(config=config, guard=guard, event_bus=event_bus, risk_guard=risk_guard)
    state_file = config.data_dir / "trailing_stops.json"
    trailing_manager = TrailingStopManager(
        broker=broker,
        event_bus=event_bus,
        state_file=state_file,
    )

    try:
        await broker.connect()
        await trailing_manager.start()

        stop_id = await trailing_manager.create_trailing_stop(trailing_config, initial_price)

        logger.info("=" * 70)
        logger.info("TRAILING STOP CREATED")
        logger.info("=" * 70)
        logger.info(f"Stop ID: {stop_id}")
        logger.info(f"Symbol: {trailing_config.symbol}")
        logger.info(f"Side: {trailing_config.side.value}")
        logger.info(f"Quantity: {trailing_config.quantity}")
        if trailing_config.trail_amount:
            logger.info(f"Trail Amount: ${trailing_config.trail_amount}")
        else:
            logger.info(f"Trail Percent: {trailing_config.trail_percent}%")
        if trailing_config.activation_price:
            logger.info(f"Activation Price: ${trailing_config.activation_price}")
        logger.info("=" * 70)
        logger.info("")
        logger.warning(
            "⚠ NOTE: This trailing stop has been created but will NOT be monitored "
            "after this command exits."
        )
        logger.warning(
            "To enable continuous monitoring and adjustment, you must run a strategy "
            "that integrates TrailingStopManager, or use 'ibkr-trader run' with "
            "appropriate configuration."
        )
    finally:
        await trailing_manager.stop()
        await broker.disconnect()


@trading_app.command("oco-order")
def oco_order_command(
    symbol: str = typer.Option(..., "--symbol", "-s", help="Symbol to trade"),
    quantity: int = typer.Option(
        1,
        "--quantity",
        "-q",
        min=1,
        help="Order quantity (must be same for both orders)",
    ),
    order_a_side: OrderSide = typer.Option(
        ...,
        "--order-a-side",
        help="First order side (BUY or SELL)",
    ),
    order_a_type: OrderType = typer.Option(
        OrderType.LIMIT,
        "--order-a-type",
        help="First order type (LIMIT/STOP)",
    ),
    order_a_price: str = typer.Option(
        ...,
        "--order-a-price",
        help="First order limit/stop price",
    ),
    order_b_side: OrderSide = typer.Option(
        ...,
        "--order-b-side",
        help="Second order side (BUY or SELL)",
    ),
    order_b_type: OrderType = typer.Option(
        OrderType.LIMIT,
        "--order-b-type",
        help="Second order type (LIMIT/STOP)",
    ),
    order_b_price: str = typer.Option(
        ...,
        "--order-b-price",
        help="Second order limit/stop price",
    ),
    group_id: str | None = typer.Option(
        None,
        "--group-id",
        help="Optional OCO group identifier (auto-generated if not provided)",
    ),
) -> None:
    """Submit an OCO (One-Cancels-Other) order pair for paper trading.

    An OCO order consists of two orders where if one fills, the other is
    automatically cancelled. Useful for entering positions at different price
    levels or managing exits.

    Common use cases:
    - Enter long OR short (bracket around consolidation)
    - Take profit at target OR stop loss (alternative to bracket)
    - Scale out at multiple levels

    Example (bracket entry - enter at 145 OR 155):
      ibkr-trader oco-order --symbol AAPL --quantity 10 \\
        --order-a-side BUY --order-a-type LIMIT --order-a-price 145.00 \\
        --order-b-side BUY --order-b-type LIMIT --order-b-price 155.00

    Example (exit management - stop loss OR take profit):
      ibkr-trader oco-order --symbol AAPL --quantity 10 \\
        --order-a-side SELL --order-a-type STOP --order-a-price 145.00 \\
        --order-b-side SELL --order-b-type LIMIT --order-b-price 155.00

    NOTE: This command creates the OCO pair but does not monitor it.
    You must run 'ibkr-trader run' or integrate OCOOrderManager in your strategy
    to enable continuous monitoring.
    """
    from ibkr_trader.cli_commands.utils import setup_logging

    config = load_config()
    setup_logging(config.log_dir, verbose=False)

    if config.trading_mode != TradingMode.PAPER:
        logger.error(
            "oco-order command is restricted to PAPER trading mode. "
            "Set IBKR_TRADING_MODE=paper before running this command."
        )
        raise typer.Exit(code=1)

    # Parse prices
    try:
        order_a_price_decimal = Decimal(order_a_price)
        order_b_price_decimal = Decimal(order_b_price)
    except (InvalidOperation, ValueError) as exc:
        raise typer.BadParameter("Invalid price format") from exc

    # Generate group ID if not provided
    if group_id is None:
        group_id = f"OCO_{symbol}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"

    # Create broker components
    guard = LiveTradingGuard(config=config, live_flag_enabled=False)
    _, risk_guard, _ = build_portfolio_and_risk_guard(config)
    guard.acknowledge_live_trading()

    try:
        asyncio.run(
            create_oco_order(
                config=config,
                guard=guard,
                risk_guard=risk_guard,
                symbol=symbol,
                quantity=quantity,
                order_a_side=order_a_side,
                order_a_type=order_a_type,
                order_a_price=order_a_price_decimal,
                order_b_side=order_b_side,
                order_b_type=order_b_type,
                order_b_price=order_b_price_decimal,
                group_id=group_id,
            )
        )
    except Exception as exc:  # pragma: no cover - surface detailed CLI error
        logger.error(f"Failed to create OCO order: {exc}")
        raise typer.Exit(code=1) from exc


async def create_oco_order(
    config: IBKRConfig,  # noqa: F821
    guard: LiveTradingGuard,
    risk_guard: RiskGuard,
    symbol: str,
    quantity: int,
    order_a_side: OrderSide,
    order_a_type: OrderType,
    order_a_price: Decimal,
    order_b_side: OrderSide,
    order_b_type: OrderType,
    order_b_price: Decimal,
    group_id: str,
) -> None:
    """Create an OCO order pair."""
    from ibkr_trader.oco_orders import OCOOrderManager

    event_bus = EventBus()
    broker = IBKRBroker(config=config, guard=guard, event_bus=event_bus, risk_guard=risk_guard)
    state_file = config.data_dir / "oco_orders.json"
    oco_manager = OCOOrderManager(
        broker=broker,
        event_bus=event_bus,
        state_file=state_file,
    )

    try:
        await broker.connect()
        await oco_manager.start()

        # Create order A
        order_a_limit = (
            order_a_price if order_a_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) else None
        )
        order_a_stop = (
            order_a_price if order_a_type in (OrderType.STOP, OrderType.STOP_LIMIT) else None
        )
        order_a = OrderRequest(
            contract=SymbolContract(symbol=symbol),
            side=order_a_side,
            quantity=quantity,
            order_type=order_a_type,
            limit_price=order_a_limit,
            stop_price=order_a_stop,
            expected_price=order_a_price,
        )

        # Create order B
        order_b_limit = (
            order_b_price if order_b_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) else None
        )
        order_b_stop = (
            order_b_price if order_b_type in (OrderType.STOP, OrderType.STOP_LIMIT) else None
        )
        order_b = OrderRequest(
            contract=SymbolContract(symbol=symbol),
            side=order_b_side,
            quantity=quantity,
            order_type=order_b_type,
            limit_price=order_b_limit,
            stop_price=order_b_stop,
            expected_price=order_b_price,
        )

        # Create OCO request
        oco_request = OCOOrderRequest(
            order_a=order_a,
            order_b=order_b,
            group_id=group_id,
        )

        result_group_id = await oco_manager.place_oco_order(oco_request)

        logger.info("=" * 70)
        logger.info("OCO ORDER CREATED")
        logger.info("=" * 70)
        logger.info(f"Group ID: {result_group_id}")
        logger.info(f"Symbol: {symbol}")
        logger.info(f"Quantity: {quantity}")
        logger.info("")
        logger.info("Order A:")
        logger.info(f"  Side: {order_a_side.value}")
        logger.info(f"  Type: {order_a_type.value}")
        logger.info(f"  Price: ${order_a_price}")
        logger.info("")
        logger.info("Order B:")
        logger.info(f"  Side: {order_b_side.value}")
        logger.info(f"  Type: {order_b_type.value}")
        logger.info(f"  Price: ${order_b_price}")
        logger.info("=" * 70)
        logger.info("")
        logger.warning(
            "⚠ NOTE: This OCO pair has been created but will NOT be monitored "
            "after this command exits."
        )
        logger.warning(
            "To enable continuous monitoring, you must run a strategy "
            "that integrates OCOOrderManager, or use 'ibkr-trader run' with "
            "appropriate configuration."
        )
    finally:
        await oco_manager.stop()
        await broker.disconnect()


@trading_app.command("set-symbol-limit")
def set_symbol_limit(
    symbol: str | None = typer.Option(
        None,
        "--symbol",
        "-s",
        help="Symbol to configure (omit with --default to update fallback limits)",
    ),
    max_position: int | None = typer.Option(
        None,
        "--max-position",
        min=0,
        help="Maximum position size (shares) allowed for the symbol",
    ),
    max_exposure: str | None = typer.Option(
        None,
        "--max-exposure",
        help="Maximum per-order notional exposure in USD",
    ),
    max_loss: str | None = typer.Option(
        None,
        "--max-loss",
        help="Maximum realized loss allowed per trading day (USD)",
    ),
    default: bool = typer.Option(
        False,
        "--default",
        help="Update the default limits applied when no symbol-specific limit exists",
    ),
) -> None:
    """Create or update per-symbol risk limits."""

    if not default and symbol is None:
        raise typer.BadParameter("Specify --symbol or pass --default to update fallback limits.")

    if default and symbol is not None:
        logger.warning("Ignoring --symbol when --default is provided")

    max_exposure_decimal: Decimal | None = None
    if max_exposure is not None:
        try:
            max_exposure_decimal = Decimal(max_exposure)
        except (InvalidOperation, ValueError) as exc:
            raise typer.BadParameter("Invalid decimal for --max-exposure.") from exc

    max_loss_decimal: Decimal | None = None
    if max_loss is not None:
        try:
            max_loss_decimal = Decimal(max_loss)
        except (InvalidOperation, ValueError) as exc:
            raise typer.BadParameter("Invalid decimal for --max-loss.") from exc

    if all(value is None for value in (max_position, max_exposure_decimal, max_loss_decimal)):
        raise typer.BadParameter(
            "Provide at least one of --max-position, --max-exposure, or --max-loss."
        )

    config = load_config()
    symbol_limits = load_symbol_limit_registry(config)

    global_max_position = config.max_position_size
    global_max_exposure = Decimal(str(config.max_order_exposure))
    global_max_daily_loss = Decimal(str(config.max_daily_loss))

    def _validate_against_global(
        label: str,
        *,
        pos: int | None,
        exposure: Decimal | None,
        loss: Decimal | None,
    ) -> None:
        if pos is not None and pos > global_max_position:
            raise typer.BadParameter(
                f"{label}: max position {pos} exceeds global limit {global_max_position}"
            )
        if exposure is not None and exposure > global_max_exposure:
            raise typer.BadParameter(
                f"{label}: max exposure {exposure} exceeds global limit {global_max_exposure}"
            )
        if loss is not None and loss > global_max_daily_loss:
            raise typer.BadParameter(
                f"{label}: max loss {loss} exceeds global limit {global_max_daily_loss}"
            )

    if default:
        _validate_against_global(
            "Default limits",
            pos=max_position,
            exposure=max_exposure_decimal,
            loss=max_loss_decimal,
        )
        symbol_limits.set_default_limit(
            max_position_size=max_position,
            max_order_exposure=max_exposure_decimal,
            max_daily_loss=max_loss_decimal,
        )
        effective_limits = symbol_limits.default_limits
        target_label = "default limits"
    else:
        assert symbol is not None
        symbol_upper = symbol.upper()
        _validate_against_global(
            f"Limits for {symbol_upper}",
            pos=max_position,
            exposure=max_exposure_decimal,
            loss=max_loss_decimal,
        )
        symbol_limits.set_symbol_limit(
            symbol=symbol_upper,
            max_position_size=max_position,
            max_order_exposure=max_exposure_decimal,
            max_daily_loss=max_loss_decimal,
        )
        effective_limits = symbol_limits.get_limit(symbol_upper)
        target_label = f"{symbol_upper} limits"

    if effective_limits is None:
        raise typer.Exit(code=1)

    target_path = symbol_limits.config_path or (config.data_dir / DEFAULT_SYMBOL_LIMITS_FILE.name)
    symbol_limits.save_config(target_path)

    typer.echo(
        f"Updated {target_label}: max_position={effective_limits.max_position_size}, "
        f"max_exposure={effective_limits.max_order_exposure}, "
        f"max_daily_loss={effective_limits.max_daily_loss}"
    )
    typer.echo(f"Configuration saved to {target_path}")


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
    strategy_choice: str = typer.Option(
        "sma",
        "--strategy",
        "-y",
        help="Strategy implementation to run (sma|adaptive_momentum|config)",
    ),
    screener_refresh_seconds: int | None = typer.Option(
        None,
        "--screener-refresh-seconds",
        help="Override refresh interval for screener-driven strategies (seconds).",
    ),
    liquidity_min_dollar_volume: float | None = typer.Option(
        None,
        "--min-dollar-volume",
        help="Minimum average daily dollar volume for liquidity screener.",
    ),
    liquidity_min_price: float | None = typer.Option(
        None,
        "--min-price",
        help="Minimum share price for liquidity screener.",
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
        "Cache TTLs -> price={} option={}",
        format_seconds(config.training_price_cache_ttl),
        format_seconds(config.training_option_cache_ttl),
    )
    logger.info(
        "IBKR snapshot limits -> max={} interval={:.2f}s",
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

    valid_strategies = {"sma", "adaptive_momentum", "config"}
    if strategy_choice not in valid_strategies:
        logger.error(
            "Unsupported strategy '{}'. Choose from {}",
            strategy_choice,
            ", ".join(sorted(valid_strategies)),
        )
        raise typer.Exit(code=1)
    if strategy_choice == "config" and config_path is None:
        logger.error("--config is required when using --strategy=config")
        raise typer.Exit(code=1)

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
            strategy_choice=strategy_choice,
            screener_refresh_seconds=screener_refresh_seconds,
            liquidity_min_dollar_volume=liquidity_min_dollar_volume,
            liquidity_min_price=liquidity_min_price,
        )
    )


async def run_strategy(
    config: IBKRConfig,  # noqa: F821
    guard: LiveTradingGuard,
    symbols: list[str],
    fast_period: int,
    slow_period: int,
    position_size: int,
    config_path: Path | None = None,
    strategy_choice: str = "sma",
    screener_refresh_seconds: int | None = None,
    liquidity_min_dollar_volume: float | None = None,
    liquidity_min_price: float | None = None,
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
    graph_config: StrategyGraphConfig | None = None
    telemetry = build_telemetry_reporter(
        event_bus=event_bus,
        file_path=config.log_dir / "telemetry.jsonl",
    )
    run_label = f"{config.trading_mode.value}-run"
    session_token = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    session_id = f"{run_label}-{session_token}"
    telemetry.update_default_context(
        {
            "run_label": run_label,
            "session_id": session_id,
        }
    )
    is_graph_path = bool(config_path and str(config_path).endswith(".graph.json"))
    telemetry.info(
        "Telemetry configured for strategy run",
        context={
            "symbols": symbols,
            "price_cache_ttl": config.training_price_cache_ttl,
            "option_cache_ttl": config.training_option_cache_ttl,
            "config_based": config_path is not None and not is_graph_path,
            "graph_config": is_graph_path,
        },
    )
    logger.info("Session ID: {}", session_id)
    kill_switch = load_kill_switch(config)
    if kill_switch.is_engaged():
        state = kill_switch.status()
        logger.error(
            "Kill switch engaged at {} due to '{}'.",
            state.triggered_at or "unknown",
            state.alert_title or "unknown alert",
        )
        if state.context:
            logger.error("Kill switch context: {}", state.context)
        logger.error(
            "Resolve using 'ibkr-trader monitoring kill-switch-clear' before restarting trading."
        )
        raise typer.Exit(code=1)

    stop_event = asyncio.Event()

    def _handle_kill(alert: AlertMessage) -> None:
        if not stop_event.is_set():
            logger.critical(
                "Kill switch engaged: {} - {} (session {})",
                alert.title,
                alert.message,
                session_id,
            )
            stop_event.set()

    market_data = MarketDataService(event_bus=event_bus)
    portfolio, risk_guard, symbol_limits = build_portfolio_and_risk_guard(config)
    screener_task: asyncio.Task[None] | None = None
    broker = IBKRBroker(
        config=config,
        guard=guard,
        event_bus=event_bus,
        risk_guard=risk_guard,
    )
    coordinator: StrategyCoordinator | None = None
    strategy: SimpleMovingAverageStrategy | ConfigBasedLiveStrategy | None = None
    order_task: asyncio.Task[None] | None = None
    execution_task: asyncio.Task[None] | None = None
    diagnostic_task: asyncio.Task[None] | None = None
    stream_contexts: list[AbstractAsyncContextManager[None]] = []

    alert_router = build_telemetry_alert_router(
        config,
        event_bus,
        kill_switch=kill_switch,
        on_kill=_handle_kill,
        session_context={"session_id": session_id, "run_label": run_label},
        history_path=config.log_dir / "alerts_history.jsonl",
    )

    try:
        await alert_router.start()
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

        if config_path is not None and str(config_path).endswith(".graph.json"):
            graph_config = load_strategy_graph(config_path)
            telemetry.info(
                "Strategy graph loaded",
                context={
                    "graph_name": graph_config.name,
                    "strategy_count": len(graph_config.strategies),
                    "session_id": session_id,
                    "run_label": run_label,
                },
            )
            symbols = sorted(
                {symbol for node in graph_config.strategies for symbol in node.symbols}
            )
            logger.info(
                "Loaded strategy graph '{}' with {} strategies ({})",
                graph_config.name,
                len(graph_config.strategies),
                ", ".join(symbols),
            )
        elif config_path is not None:
            graph_config = None
        if not config.use_mock_market_data:
            market_data.attach_ib(broker.ib)
            if graph_config is not None:
                subscribe_symbols = symbols
            else:
                subscribe_symbols = symbols
                if config_path is not None:
                    strat_config = load_strategy_config(config_path)
                    subscribe_symbols = [strat_config.symbol]

            for symbol in subscribe_symbols:
                request = SubscriptionRequest(SymbolContract(symbol=symbol))
                context = market_data.subscribe(request)
                await context.__aenter__()
                stream_contexts.append(context)

        if graph_config is not None:
            coordinator = StrategyCoordinator(
                broker=broker,
                event_bus=event_bus,
                market_data=market_data,
                risk_guard=risk_guard,
                telemetry=telemetry,
                subscribe_market_data=False,
            )
            await coordinator.start(graph_config)
            logger.info(
                "Strategy coordinator initialized with {} strategies",
                len(coordinator.strategies),
            )
        # Initialize strategy based on config or request
        elif config_path is not None and strategy_choice == "config":
            strat_config = load_strategy_config(config_path)
            logger.info(
                f"Loaded strategy config: {strat_config.name} ({strat_config.strategy_type})"
            )

            replay_strategy = StrategyFactory.create(strat_config)
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
        elif strategy_choice == "adaptive_momentum":
            adaptive_config = AdaptiveMomentumConfig(
                name="AdaptiveMomentum",
                symbols=[s.upper() for s in symbols],
                position_size=position_size,
            )
            strategy = AdaptiveMomentumStrategy(
                config=adaptive_config,
                broker=broker,
                event_bus=event_bus,
                risk_guard=risk_guard,
                telemetry=telemetry,
            )
            logger.info(
                "Adaptive momentum strategy initialized: fast={} slow={} symbols={}",
                adaptive_config.fast_lookback,
                adaptive_config.slow_lookback,
                ", ".join(adaptive_config.symbols),
            )

            refresh_interval = (
                screener_refresh_seconds
                if screener_refresh_seconds is not None
                else adaptive_config.screener_refresh_seconds
            )
            default_screen_cfg = LiquidityScreenerConfig()
            screener_config = LiquidityScreenerConfig(
                minimum_dollar_volume=Decimal(str(liquidity_min_dollar_volume))
                if liquidity_min_dollar_volume is not None
                else default_screen_cfg.minimum_dollar_volume,
                minimum_price=Decimal(str(liquidity_min_price))
                if liquidity_min_price is not None
                else default_screen_cfg.minimum_price,
                universe=[s.upper() for s in symbols],
                max_symbols=max(len(symbols), adaptive_config.max_open_positions * 2),
                lookback_days=default_screen_cfg.lookback_days,
            )
            screener = LiquidityScreener(screener_config)
            strategy.set_screener(screener)
            await strategy.refresh_universe()

            if refresh_interval and refresh_interval > 0:

                async def _screener_loop() -> None:
                    try:
                        while not stop_event.is_set():
                            try:
                                await asyncio.wait_for(stop_event.wait(), timeout=refresh_interval)
                                return
                            except TimeoutError:
                                try:
                                    await strategy.refresh_universe()
                                except Exception as exc:  # pragma: no cover - logging only
                                    logger.error("Screener refresh failed: {}", exc)
                    except asyncio.CancelledError:
                        raise

                screener_task = asyncio.create_task(_screener_loop())
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

        if strategy is not None:
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
                            "[diagnostic] {} {}",
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
            while not stop_event.is_set():
                counter += 1

                event_time = datetime.now(UTC)
                for symbol in symbols:
                    mock_price = MOCK_PRICE_BASE + Decimal(counter % MOCK_PRICE_VARIATION_MODULO)
                    await market_data.publish_price(symbol, mock_price, timestamp=event_time)

                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=MOCK_PRICE_SLEEP_SECONDS)
                except TimeoutError:
                    continue
        else:
            while not stop_event.is_set():
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        stop_event.wait(), timeout=MARKET_DATA_IDLE_SLEEP_SECONDS
                    )

    except KeyboardInterrupt:
        stop_event.set()
        logger.info("=" * 70)
        logger.warning("SHUTDOWN INITIATED BY USER")
        logger.info("=" * 70)
    except Exception as e:
        stop_event.set()
        logger.error(f"Error during execution: {e}")
        raise
    finally:
        stop_event.set()
        with suppress(Exception):
            await alert_router.stop()
        if kill_switch.cancel_orders_enabled and kill_switch.is_engaged():
            with suppress(Exception):
                await broker.cancel_all_orders()
        if screener_task is not None:
            screener_task.cancel()
            with suppress(asyncio.CancelledError):
                await screener_task
        if coordinator is not None:
            await coordinator.stop()
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
