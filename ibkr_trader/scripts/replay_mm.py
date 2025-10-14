"""CLI entry point for replaying market microstructure datasets."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import typer
from loguru import logger

from ibkr_trader.sim.events import EventLoader
from ibkr_trader.sim.runner import ReplayRunner, ReplayStrategy
from ibkr_trader.sim.strategies import FixedSpreadMMStrategy
from ibkr_trader.strategy_configs import StrategyConfig, StrategyFactory

app = typer.Typer(help="Replay recorded market depth data with configurable strategies.")


@dataclass(slots=True)
class ReplayStats:
    fills: int = 0
    total_qty: int = 0
    inventory: int = 0


async def _run_replay(
    order_book_files: list[Path],
    trade_files: list[Path],
    option_surface_files: list[Path],
    strategy: ReplayStrategy,
) -> ReplayStats:
    loader = EventLoader(
        order_book_files=order_book_files,
        trade_files=trade_files,
        option_surface_files=option_surface_files,
    )
    runner = ReplayRunner(loader=loader, strategy=strategy)
    await runner.run()

    return ReplayStats(
        fills=getattr(strategy, "fills", 0),
        total_qty=getattr(strategy, "total_filled_qty", 0),
        inventory=getattr(strategy, "inventory", 0),
    )


@app.command()
def run(
    config: Path | None = typer.Option(
        None,
        exists=True,
        readable=True,
        help="Optional JSON config file describing the strategy.",
    ),
    order_book: list[Path] | None = typer.Option(
        None,
        exists=True,
        readable=True,
        help="Path(s) to CSV order book snapshots (flattened).",
    ),
    trades: list[Path] | None = typer.Option(
        None,
        exists=True,
        readable=True,
        help="Optional path(s) to trade prints CSVs.",
    ),
    option_surface: list[Path] | None = typer.Option(
        None,
        exists=True,
        readable=True,
        help="Optional path(s) to option surface CSV snapshots.",
    ),
    symbol: str = typer.Option(
        "", "--symbol", "-s", help="Underlying symbol to trade (when not using config)."
    ),
    spread: float = typer.Option(
        0.20, "--spread", help="Total spread (USD) between bid and ask quotes."
    ),
    quote_size: int = typer.Option(1, "--quote-size", help="Quantity per quote."),
    inventory_limit: int = typer.Option(
        5, "--inventory-limit", help="Maximum inventory before pausing quotes."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging."),
) -> None:
    """Replay a market-making strategy over recorded data."""

    logger.remove()
    logger.add(lambda msg: typer.echo(msg, nl=False), level="DEBUG" if verbose else "INFO")

    if config is not None:
        cfg = StrategyConfig.load(config)
        strategy = StrategyFactory.create(cfg)
        order_book_files = list(cfg.data.order_book)
        trade_files = list(cfg.data.trades)
        option_surface_files = list(cfg.data.option_surface)
        symbol_label = cfg.symbol
    else:
        if not order_book:
            raise typer.BadParameter("Provide --order-book or --config", param_hint="--order-book")
        strategy = FixedSpreadMMStrategy(
            symbol=symbol or "UNKNOWN",
            quote_size=quote_size,
            spread=spread,
            inventory_limit=inventory_limit,
        )
        order_book_files = order_book
        trade_files = trades or []
        option_surface_files = option_surface or []
        symbol_label = symbol or "unknown"

    logger.info(
        "Starting replay for symbol={} strategy={}", symbol_label, strategy.__class__.__name__
    )
    start_time = datetime.now(UTC)

    stats = asyncio.run(
        _run_replay(
            order_book_files=order_book_files,
            trade_files=trade_files,
            option_surface_files=option_surface_files,
            strategy=strategy,
        )
    )

    elapsed = (datetime.now(UTC) - start_time).total_seconds()
    logger.info("Replay completed in {:.2f}s", elapsed)
    logger.info("Total fills: {}", stats.fills)
    logger.info("Filled quantity: {}", stats.total_qty)
    logger.info("Ending inventory: {}", stats.inventory)


if __name__ == "__main__":
    app()
