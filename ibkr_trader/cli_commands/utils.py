"""Shared utility functions for CLI commands."""

import json
import sys
from collections import deque
from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import typer
from loguru import logger

from ibkr_trader.constants import (
    DEFAULT_CORRELATION_MATRIX_FILE,
    DEFAULT_PORTFOLIO_SNAPSHOT,
    DEFAULT_SYMBOL_LIMITS_FILE,
)
from ibkr_trader.core.alerting import (
    AlertMessage,
    AlertTransport,
    LogAlertTransport,
    TelemetryAlertConfig,
    TelemetryAlertRouter,
    WebhookAlertTransport,
)
from ibkr_trader.core.events import EventBus
from ibkr_trader.core.kill_switch import KillSwitch
from ibkr_trader.portfolio import PortfolioState, RiskGuard, SymbolLimitRegistry
from ibkr_trader.risk import CorrelationMatrix, CorrelationRiskGuard
from ibkr_trader.summary import summarize_run
from ibkr_trader.telemetry import TelemetryReporter
from model.data import (
    FileCacheStore,
    IBKRMarketDataSource,
    IBKROptionChainSource,
    MarketDataClient,
    OptionChainCacheStore,
    OptionChainClient,
    YFinanceMarketDataSource,
    YFinanceOptionChainSource,
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


def load_symbol_limit_registry(config: "IBKRConfig") -> SymbolLimitRegistry:  # noqa: F821
    """Load symbol limit registry for the given configuration."""

    symbol_limit_path = config.data_dir / DEFAULT_SYMBOL_LIMITS_FILE.name
    return SymbolLimitRegistry(config_path=symbol_limit_path)


def build_portfolio_and_risk_guard(
    config: "IBKRConfig",  # noqa: F821
) -> tuple[PortfolioState, RiskGuard, SymbolLimitRegistry]:
    """Instantiate portfolio state, symbol limits, and risk guard."""

    snapshot_path = config.data_dir / DEFAULT_PORTFOLIO_SNAPSHOT.name
    symbol_limits = load_symbol_limit_registry(config)

    correlation_guard: CorrelationRiskGuard | None = None
    max_correlated = getattr(config, "max_correlated_exposure", None)
    if max_correlated:
        matrix_path = config.data_dir / DEFAULT_CORRELATION_MATRIX_FILE.name
        matrix = CorrelationMatrix.load(matrix_path)
        if matrix is None:
            logger.debug(
                "No correlation matrix found at {}; correlation guard disabled",
                matrix_path,
            )
        else:
            threshold = float(getattr(config, "correlation_threshold", 0.75))
            try:
                correlation_guard = CorrelationRiskGuard(
                    correlation_matrix=matrix,
                    max_correlated_exposure=Decimal(str(max_correlated)),
                    threshold=threshold,
                )
            except ValueError as exc:
                logger.warning(
                    "Correlation guard disabled due to invalid configuration: {}",
                    exc,
                )

    fee_config = (
        config.create_fee_config() if getattr(config, "enable_fee_estimates", False) else None
    )

    portfolio = PortfolioState(
        Decimal(str(config.max_daily_loss)),
        snapshot_path=snapshot_path,
        fee_config=fee_config,
    )
    risk_guard = RiskGuard(
        portfolio=portfolio,
        max_exposure=Decimal(str(config.max_order_exposure)),
        symbol_limits=symbol_limits,
        correlation_guard=correlation_guard,
        fee_config=fee_config,
    )
    return portfolio, risk_guard, symbol_limits


def build_alert_transport(config: "IBKRConfig") -> AlertTransport:  # noqa: F821
    """Construct an alert transport for central routing."""

    if getattr(config, "alerting_webhook", None):
        return WebhookAlertTransport(
            config.alerting_webhook,  # type: ignore[arg-type]
            verify_ssl=bool(getattr(config, "alerting_verify_ssl", True)),
        )
    return LogAlertTransport()


def load_kill_switch(config: "IBKRConfig") -> KillSwitch:  # noqa: F821
    """Load persistent kill switch state for configuration."""

    state_path = config.data_dir / "kill_switch.json"
    cancel_flag = bool(getattr(config, "kill_switch_cancel_orders", True))
    return KillSwitch(state_path, cancel_orders_enabled=cancel_flag)


def build_telemetry_alert_router(
    config: "IBKRConfig",  # noqa: F821
    event_bus: EventBus,
    *,
    kill_switch: KillSwitch | None = None,
    on_kill: Callable[["AlertMessage"], None] | None = None,
    enable_kill_switch: bool = True,
    extra_context: dict[str, object] | None = None,
    history_path: Path | None = None,
    session_context: dict[str, object] | None = None,
) -> TelemetryAlertRouter:
    """Create a telemetry alert router wired to the CLI event bus."""

    transport = build_alert_transport(config)
    alert_config = TelemetryAlertConfig(
        trailing_rate_limit_threshold=max(1, int(config.trailing_stop_alert_threshold)),
        trailing_rate_limit_window=timedelta(
            seconds=max(1, int(config.trailing_stop_alert_window_seconds))
        ),
        trailing_rate_limit_cooldown=timedelta(
            seconds=max(1, int(config.trailing_stop_alert_cooldown_seconds))
        ),
        screener_stale_after=timedelta(seconds=max(0, int(config.screener_alert_stale_seconds))),
        screener_check_interval=timedelta(seconds=max(1, int(config.screener_alert_check_seconds))),
    )
    ks = load_kill_switch(config) if enable_kill_switch else None
    if enable_kill_switch and kill_switch is not None:
        ks = kill_switch
    history = history_path or (config.log_dir / "alerts_history.jsonl")
    merged_context = dict(extra_context or {})
    if session_context:
        merged_context.update(session_context)
    return TelemetryAlertRouter(
        event_bus=event_bus,
        transport=transport,
        config=alert_config,
        kill_switch=ks,
        on_kill=on_kill,
        extra_context=merged_context,
        history_path=history,
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


def format_seconds(value: float | None) -> str:
    """Format a duration in seconds as a human-readable string."""
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


def format_telemetry_line(line: str) -> str:
    """Format a JSON telemetry line for display."""
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


def load_portfolio_snapshot(path: Path) -> dict[str, object] | None:
    """Load portfolio snapshot from JSON file."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # pragma: no cover - defensive
        return None


def tail_telemetry_entries(telemetry_file: Path, tail: int) -> list[str]:
    """Load the last N telemetry entries from a file."""
    if not telemetry_file.exists():
        return []
    with telemetry_file.open("r", encoding="utf-8") as handle:
        lines = list(deque(handle, maxlen=tail if tail > 0 else None))
    formatted: list[str] = []
    for line in lines:
        formatted_line = format_telemetry_line(line)
        if formatted_line:
            formatted.append(formatted_line)
    return formatted


def emit_run_summary(
    *,
    config: "IBKRConfig",  # noqa: F821
    telemetry: TelemetryReporter,
    label: str,
    tail: int = 100,
) -> None:
    """Emit a summary of the run with portfolio and telemetry stats."""
    from ibkr_trader.constants import DEFAULT_PORTFOLIO_SNAPSHOT

    snapshot_path = config.data_dir / DEFAULT_PORTFOLIO_SNAPSHOT.name
    telemetry_file = config.log_dir / "telemetry.jsonl"
    summary = summarize_run(snapshot_path, tail_telemetry_entries(telemetry_file, tail))

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
        logger.info("Recommended actions ({}):", label)
        for action in summary.recommended_actions:
            logger.info("  - {}", action)
    if summary.trade_stats:
        logger.info("Trade statistics ({}): {}", label, summary.trade_stats)
