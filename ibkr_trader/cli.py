"""CLI entry point for IBKR Personal Trader."""

import typer

from ibkr_trader.cli_commands.data import data_app
from ibkr_trader.cli_commands.monitoring import monitoring_app
from ibkr_trader.cli_commands.trading import trading_app

app = typer.Typer(
    name="ibkr-trader",
    help="IBKR Personal Trading Platform - Paper trading by default",
)

# Add subcommand groups (namespaces)
app.add_typer(trading_app, name="trading")
app.add_typer(monitoring_app, name="monitoring")
app.add_typer(data_app, name="data")


def _register_root_aliases(source_app: typer.Typer) -> None:
    """Register commands from source_app at the root level for backward compatibility."""

    for cmd in source_app.registered_commands:
        callback = cmd.callback
        if callback is None:
            continue
        command_name = cmd.name or callback.__name__.replace("_", "-")
        decorator = app.command(  # type: ignore[misc]
            name=command_name,
            help=cmd.help,
            short_help=cmd.short_help,
            add_help_option=cmd.add_help_option,
            hidden=cmd.hidden,
            deprecated=cmd.deprecated,
            rich_help_panel=cmd.rich_help_panel,
            no_args_is_help=cmd.no_args_is_help,
            context_settings=cmd.context_settings,
        )
        decorator(callback)


_register_root_aliases(trading_app)
_register_root_aliases(monitoring_app)
_register_root_aliases(data_app)


if __name__ == "__main__":
    app()
