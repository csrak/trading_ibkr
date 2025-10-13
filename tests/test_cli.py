"""CLI tests for paper order submission."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from ibkr_trader import cli
from ibkr_trader.config import IBKRConfig, TradingMode
from ibkr_trader.models import OrderRequest, OrderResult, OrderStatus


runner = CliRunner()


class DummyBroker:
    """Stub broker that records the last order request."""

    def __init__(self, config: IBKRConfig, guard: object) -> None:
        self.config = config
        self.guard = guard
        self.connected = False
        self.last_order: OrderResult | None = None
        self.preview_called = False

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def place_order(self, order_request: OrderRequest) -> OrderResult:
        assert self.connected
        result = OrderResult(
            order_id=999,
            contract=order_request.contract,
            side=order_request.side,
            quantity=order_request.quantity,
            order_type=order_request.order_type,
            status=OrderStatus.SUBMITTED,
        )
        self.last_order = result
        return result

    async def preview_order(self, order_request: OrderRequest) -> object:
        assert self.connected
        self.preview_called = True
        self.last_order = OrderResult(
            order_id=0,
            contract=order_request.contract,
            side=order_request.side,
            quantity=order_request.quantity,
            order_type=order_request.order_type,
            status=OrderStatus.PENDING,
        )
        return object()


def test_paper_order_cli_submits_market_order(monkeypatch: pytest.MonkeyPatch) -> None:
    config = IBKRConfig(trading_mode=TradingMode.PAPER)

    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(cli, "setup_logging", lambda *_args, **_kwargs: None)
    dummy_broker = DummyBroker(config=config, guard=None)
    monkeypatch.setattr(cli, "IBKRBroker", lambda config, guard: dummy_broker)

    result = runner.invoke(
        cli.app,
        ["paper-order", "--symbol", "AAPL", "--quantity", "2"],
    )

    assert result.exit_code == 0, result.stdout
    assert dummy_broker.last_order is not None
    assert dummy_broker.last_order.quantity == 2
    assert dummy_broker.last_order.contract.symbol == "AAPL"
    assert not dummy_broker.preview_called


def test_paper_order_requires_limit_price(monkeypatch: pytest.MonkeyPatch) -> None:
    config = IBKRConfig(trading_mode=TradingMode.PAPER)
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(cli, "setup_logging", lambda *_args, **_kwargs: None)

    result = runner.invoke(
        cli.app,
        ["paper-order", "--symbol", "AAPL", "--type", "LIMIT"],
    )

    assert result.exit_code != 0
    assert "Limit price is required" in result.stdout or result.stderr


def test_paper_order_invalid_limit_format(monkeypatch: pytest.MonkeyPatch) -> None:
    config = IBKRConfig(trading_mode=TradingMode.PAPER)
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(cli, "setup_logging", lambda *_args, **_kwargs: None)

    result = runner.invoke(
        cli.app,
        ["paper-order", "--symbol", "AAPL", "--type", "LIMIT", "--limit", "abc"],
    )

    assert result.exit_code != 0
    assert "Invalid limit price format" in result.stdout or result.stderr


def test_paper_order_rejects_live_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    config = IBKRConfig(trading_mode=TradingMode.LIVE)
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(cli, "setup_logging", lambda *_args, **_kwargs: None)

    result = runner.invoke(
        cli.app,
        ["paper-order", "--symbol", "AAPL"],
    )

    assert result.exit_code != 0
    assert "restricted to PAPER trading mode" in result.stdout or result.stderr


def test_paper_order_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    config = IBKRConfig(trading_mode=TradingMode.PAPER)

    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(cli, "setup_logging", lambda *_args, **_kwargs: None)
    dummy_broker = DummyBroker(config=config, guard=None)
    monkeypatch.setattr(cli, "IBKRBroker", lambda config, guard: dummy_broker)

    result = runner.invoke(
        cli.app,
        ["paper-order", "--symbol", "AAPL", "--quantity", "1", "--preview"],
    )

    assert result.exit_code == 0, result.stdout
    assert dummy_broker.preview_called
