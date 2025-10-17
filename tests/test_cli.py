"""CLI tests for paper order submission."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from model.data.options import OptionChain
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
        return SimpleNamespace(
            initMarginChange="0.00",
            maintMarginChange="0.00",
            equityWithLoanChange="0.00",
            commission="0.00",
        )


class DummyMarketDataService:
    def __init__(self, event_bus: object) -> None:
        self.event_bus = event_bus

    async def publish_price(self, symbol: str, price: object) -> None:
        return None

    def attach_ib(self, ib: object) -> None:  # pragma: no cover - tests mock IB usage
        return None

    def subscribe(self, *args: object, **kwargs: object) -> object:
        @asynccontextmanager
        async def _context() -> None:
            yield

        return _context()


def test_paper_order_cli_submits_market_order(monkeypatch: pytest.MonkeyPatch) -> None:
    config = IBKRConfig(trading_mode=TradingMode.PAPER)

    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(cli, "setup_logging", lambda *_args, **_kwargs: None)
    dummy_broker = DummyBroker(config=config, guard=None)
    monkeypatch.setattr(
        cli,
        "IBKRBroker",
        lambda config, guard, event_bus=None, risk_guard=None: dummy_broker,
    )
    monkeypatch.setattr(
        cli, "MarketDataService", lambda event_bus: DummyMarketDataService(event_bus)
    )

    result = runner.invoke(
        cli.app,
        ["paper-order", "--symbol", "AAPL", "--quantity", "2"],
    )

    assert result.exit_code == 0, result.stdout
    assert dummy_broker.last_order is not None
    assert dummy_broker.last_order.quantity == 2
    assert dummy_broker.last_order.contract.symbol == "AAPL"
    assert dummy_broker.last_order.contract.sec_type == "STK"
    assert dummy_broker.last_order.contract.exchange == "SMART"
    assert dummy_broker.last_order.contract.currency == "USD"
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


def test_paper_order_allows_custom_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    config = IBKRConfig(trading_mode=TradingMode.PAPER)

    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(cli, "setup_logging", lambda *_args, **_kwargs: None)
    dummy_broker = DummyBroker(config=config, guard=None)
    monkeypatch.setattr(
        cli,
        "IBKRBroker",
        lambda config, guard, event_bus=None, risk_guard=None: dummy_broker,
    )
    monkeypatch.setattr(
        cli, "MarketDataService", lambda event_bus: DummyMarketDataService(event_bus)
    )

    result = runner.invoke(
        cli.app,
        [
            "paper-order",
            "--symbol",
            "EUR",
            "--sec-type",
            "CASH",
            "--exchange",
            "IDEALPRO",
            "--currency",
            "USD",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert dummy_broker.last_order is not None
    assert dummy_broker.last_order.contract.sec_type == "CASH"
    assert dummy_broker.last_order.contract.exchange == "IDEALPRO"
    assert dummy_broker.last_order.contract.currency == "USD"


def test_paper_order_rejects_live_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    config = IBKRConfig(trading_mode=TradingMode.LIVE)
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(cli, "setup_logging", lambda *_args, **_kwargs: None)

    result = runner.invoke(
        cli.app,
        ["paper-order", "--symbol", "AAPL"],
    )

    assert result.exit_code != 0


def test_paper_order_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    config = IBKRConfig(trading_mode=TradingMode.PAPER)

    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(cli, "setup_logging", lambda *_args, **_kwargs: None)
    dummy_broker = DummyBroker(config=config, guard=None)
    monkeypatch.setattr(
        cli,
        "IBKRBroker",
        lambda config, guard, event_bus=None, risk_guard=None: dummy_broker,
    )
    monkeypatch.setattr(
        cli, "MarketDataService", lambda event_bus: DummyMarketDataService(event_bus)
    )

    result = runner.invoke(
        cli.app,
        ["paper-order", "--symbol", "AAPL", "--quantity", "1", "--preview"],
    )

    assert result.exit_code == 0, result.stdout
    assert dummy_broker.preview_called


def test_train_model_cli_uses_data_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = IBKRConfig(trading_mode=TradingMode.PAPER)
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(cli, "setup_logging", lambda *_args, **_kwargs: None)

    captured: dict[str, object] = {}

    def fake_train_linear_industry_model(**kwargs: object) -> Path:
        captured.update(kwargs)
        artifact_dir = kwargs["artifact_dir"]
        assert isinstance(artifact_dir, Path)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        model_path = artifact_dir / "artifact.json"
        model_path.write_text("{}")
        predictions_path = artifact_dir / f"{kwargs['target_symbol']}_predictions.csv"
        predictions_path.write_text("timestamp,predicted_price\n")
        return model_path

    monkeypatch.setattr(cli, "train_linear_industry_model", fake_train_linear_industry_model)

    source_closed = {"value": False}
    captured_create: dict[str, object] = {}

    class DummySource:
        def close(self) -> None:
            source_closed["value"] = True

    dummy_client = object()

    def fake_create_market_data_client(
        source_name: str,
        cache_dir_param: Path,
        config_param: IBKRConfig,
        *,
        max_snapshots: int,
        snapshot_interval: float,
        client_id: int,
    ) -> tuple[object, DummySource]:
        captured_create.update(
            {
                "source_name": source_name,
                "cache_dir": cache_dir_param,
                "max_snapshots": max_snapshots,
                "snapshot_interval": snapshot_interval,
                "client_id": client_id,
                "config_id": config_param.client_id,
            }
        )
        return dummy_client, DummySource()

    monkeypatch.setattr(
        cli,
        "create_market_data_client",
        fake_create_market_data_client,
    )

    result = runner.invoke(
        cli.app,
        [
            "train-model",
            "--target",
            "AAPL",
            "--peer",
            "MSFT",
            "--start",
            "2024-01-01",
            "--end",
            "2024-02-01",
            "--artifact-dir",
            str(tmp_path / "artifacts"),
            "--cache-dir",
            str(tmp_path / "cache"),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured.get("data_client") is dummy_client
    assert source_closed["value"] is True
    assert captured_create["source_name"] == config.training_data_source
    assert captured_create["cache_dir"] == (tmp_path / "cache")
    assert captured_create["max_snapshots"] == config.training_max_snapshots
    assert captured_create["snapshot_interval"] == config.training_snapshot_interval
    assert captured_create["client_id"] == config.training_client_id


def test_train_model_cli_allows_ibkr_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = IBKRConfig(
        trading_mode=TradingMode.PAPER,
        training_data_source="ibkr",
        training_client_id=222,
        training_max_snapshots=5,
        training_snapshot_interval=2.0,
    )
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(cli, "setup_logging", lambda *_args, **_kwargs: None)

    captured_kwargs: dict[str, object] = {}

    def fake_train(**kwargs: object) -> Path:
        captured_kwargs.update(kwargs)
        artifact_dir = Path(kwargs["artifact_dir"])
        artifact_dir.mkdir(parents=True, exist_ok=True)
        model_path = artifact_dir / "artifact.json"
        model_path.write_text("{}")
        (artifact_dir / f"{kwargs['target_symbol']}_predictions.csv").write_text(
            "timestamp,predicted_price\n"
        )
        return model_path

    monkeypatch.setattr(cli, "train_linear_industry_model", fake_train)

    source_closed = {"value": False}

    class DummySource:
        def close(self) -> None:
            source_closed["value"] = True

    create_calls: dict[str, object] = {}

    def fake_create(
        source_name: str,
        cache_dir_param: Path,
        config_param: IBKRConfig,
        *,
        max_snapshots: int,
        snapshot_interval: float,
        client_id: int,
    ) -> tuple[object, DummySource]:
        create_calls.update(
            {
                "source_name": source_name,
                "cache_dir": cache_dir_param,
                "max_snapshots": max_snapshots,
                "snapshot_interval": snapshot_interval,
                "client_id": client_id,
            }
        )
        return object(), DummySource()

    monkeypatch.setattr(cli, "create_market_data_client", fake_create)

    result = runner.invoke(
        cli.app,
        [
            "train-model",
            "--target",
            "AAPL",
            "--peer",
            "MSFT",
            "--start",
            "2024-01-01",
            "--end",
            "2024-02-01",
            "--data-source",
            "ibkr",
            "--cache-dir",
            str(tmp_path / "ibkr-cache"),
            "--max-snapshots",
            "3",
            "--snapshot-interval",
            "0.5",
            "--ibkr-client-id",
            "333",
            "--artifact-dir",
            str(tmp_path / "artifacts"),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert create_calls["source_name"] == "ibkr"
    assert create_calls["cache_dir"] == (tmp_path / "ibkr-cache")
    assert create_calls["max_snapshots"] == 3
    assert create_calls["snapshot_interval"] == 0.5
    assert create_calls["client_id"] == 333
    assert captured_kwargs["data_client"] is not None
    assert source_closed["value"] is True


def test_train_model_cli_rejects_unknown_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = IBKRConfig(trading_mode=TradingMode.PAPER)
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(cli, "setup_logging", lambda *_args, **_kwargs: None)

    monkeypatch.setattr(
        cli,
        "train_linear_industry_model",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not train")),
    )

    result = runner.invoke(
        cli.app,
        [
            "train-model",
            "--target",
            "AAPL",
            "--peer",
            "MSFT",
            "--start",
            "2024-01-01",
            "--end",
            "2024-02-01",
            "--data-source",
            "invalid",
            "--artifact-dir",
            str(tmp_path / "artifacts"),
            "--cache-dir",
            str(tmp_path / "cache"),
        ],
    )

    assert result.exit_code != 0
    assert "Unsupported data source" in result.stdout or result.stderr


def test_cache_option_chain_cli_invokes_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = IBKRConfig(trading_mode=TradingMode.PAPER)
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(cli, "setup_logging", lambda *_args, **_kwargs: None)

    captured_create: dict[str, object] = {}

    class DummyOptionClient:
        def __init__(self) -> None:
            self.requests: list[object] = []

        def get_option_chain(self, request: object) -> OptionChain:
            self.requests.append(request)
            calls = pd.DataFrame({"strike": [100.0], "right": ["C"], "bid": [1.0], "ask": [1.2]})
            puts = pd.DataFrame({"strike": [100.0], "right": ["P"], "bid": [0.9], "ask": [1.1]})
            return OptionChain(calls=calls, puts=puts)

    source_closed = {"value": False}

    class DummySource:
        def close(self) -> None:
            source_closed["value"] = True

    def fake_create_option_chain_client(
        source_name: str,
        cache_dir_param: Path,
        config_param: IBKRConfig,
        *,
        max_snapshots: int,
        snapshot_interval: float,
        client_id: int,
    ) -> tuple[DummyOptionClient, DummySource]:
        captured_create.update(
            {
                "source_name": source_name,
                "cache_dir": cache_dir_param,
                "max_snapshots": max_snapshots,
                "snapshot_interval": snapshot_interval,
                "client_id": client_id,
            }
        )
        return DummyOptionClient(), DummySource()

    monkeypatch.setattr(cli, "create_option_chain_client", fake_create_option_chain_client)

    result = runner.invoke(
        cli.app,
        [
            "cache-option-chain",
            "--symbol",
            "AAPL",
            "--expiry",
            "2024-01-19",
            "--data-source",
            "yfinance",
            "--cache-dir",
            str(tmp_path / "option-cache"),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured_create["source_name"] == "yfinance"
    assert captured_create["cache_dir"] == (tmp_path / "option-cache")
    assert captured_create["max_snapshots"] == config.training_max_snapshots
    assert captured_create["snapshot_interval"] == config.training_snapshot_interval
    assert captured_create["client_id"] == config.training_client_id
    assert source_closed["value"] is True


def test_paper_quick_lists_presets() -> None:
    result = runner.invoke(
        cli.app,
        ["paper-quick", "--list-presets", "eurusd"],
    )

    assert result.exit_code == 0
    assert "Available presets" in result.stdout


def test_paper_quick_uses_preset_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    config = IBKRConfig(trading_mode=TradingMode.PAPER)
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(cli, "setup_logging", lambda *_args, **_kwargs: None)
    dummy_broker = DummyBroker(config=config, guard=None)
    monkeypatch.setattr(
        cli,
        "IBKRBroker",
        lambda config, guard, event_bus=None, risk_guard=None: dummy_broker,
    )
    monkeypatch.setattr(
        cli, "MarketDataService", lambda event_bus: DummyMarketDataService(event_bus)
    )

    result = runner.invoke(
        cli.app,
        ["paper-quick", "eurusd", "--preview"],
    )

    assert result.exit_code == 0, result.stdout
    assert dummy_broker.preview_called
    assert dummy_broker.last_order is not None
    assert dummy_broker.last_order.contract.currency == "USD"
    assert dummy_broker.last_order.quantity == 10_000


def test_paper_quick_unknown_preset(monkeypatch: pytest.MonkeyPatch) -> None:
    config = IBKRConfig(trading_mode=TradingMode.PAPER)
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(cli, "setup_logging", lambda *_args, **_kwargs: None)

    result = runner.invoke(
        cli.app,
        ["paper-quick", "does-not-exist"],
    )

    assert result.exit_code != 0
