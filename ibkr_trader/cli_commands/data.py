"""CLI commands for data operations (backtest, train-model, cache-option-chain)."""

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
import typer
from loguru import logger

from ibkr_trader.backtest.engine import BacktestEngine
from ibkr_trader.config import load_config
from ibkr_trader.constants import DEFAULT_PORTFOLIO_SNAPSHOT
from ibkr_trader.events import EventBus
from ibkr_trader.portfolio import PortfolioState, RiskGuard
from ibkr_trader.sim.broker import SimulatedBroker, SimulatedMarketData
from ibkr_trader.strategy import (
    IndustryModelConfig,
    IndustryModelStrategy,
    SimpleMovingAverageStrategy,
    SMAConfig,
)
from ibkr_trader.telemetry import build_telemetry_reporter
from model.data import (
    MarketDataClient,
    OptionChainClient,
    OptionChainRequest,
    SnapshotLimitError,
)
from model.training.industry_model import train_linear_industry_model

from .utils import (
    create_market_data_client,
    create_option_chain_client,
    emit_run_summary,
    setup_logging,
)

data_app = typer.Typer()


@data_app.command()
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
    emit_run_summary(config=config, telemetry=telemetry, label="backtest")


@data_app.command("train-model")
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


@data_app.command("cache-option-chain")
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
