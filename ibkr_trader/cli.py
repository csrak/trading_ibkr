"""CLI entry point for IBKR Personal Trader."""

import typer

from ibkr_trader.cli_commands.data import data_app
from ibkr_trader.cli_commands.monitoring import monitoring_app
from ibkr_trader.cli_commands.trading import trading_app

app = typer.Typer(
    name="ibkr-trader",
    help="IBKR Personal Trading Platform - Paper trading by default",
)

# Register command groups
# Note: Commands are also registered at root level for backward compatibility

# Register all trading commands at root level (backward compatibility)
for cmd in trading_app.registered_commands:
    app.registered_commands.append(cmd)

# Register all monitoring commands at root level (backward compatibility)
for cmd in monitoring_app.registered_commands:
    app.registered_commands.append(cmd)

# Register all data commands at root level (backward compatibility)
for cmd in data_app.registered_commands:
    app.registered_commands.append(cmd)


if __name__ == "__main__":
    app()
