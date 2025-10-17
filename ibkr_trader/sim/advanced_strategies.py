"""Advanced replay strategies implementing configurable behaviours."""

from __future__ import annotations

from collections import deque
from statistics import fmean, pstdev

from ibkr_trader.base_strategy import BrokerProtocol
from ibkr_trader.models import OrderRequest, OrderSide, OrderType, SymbolContract
from ibkr_trader.sim.runner import ReplayStrategy
from ibkr_trader.strategy_configs.config import (
    MeanReversionConfig,
    MicrostructureMLConfig,
    RegimeRotationConfig,
    SkewArbitrageConfig,
    VolatilityOverlayConfig,
    VolSpilloverConfig,
)
from model.data.models import OptionSurfaceEntry, OrderBookSnapshot, TradeEvent


class MeanReversionStrategy(ReplayStrategy):
    """Simple mean reversion strategy using z-score thresholds."""

    def __init__(self, config: MeanReversionConfig) -> None:
        self.config = config
        self.short_window = config.execution.lookback_short
        self.long_window = max(config.execution.lookback_long, self.short_window + 1)
        self.entry_zscore = config.execution.entry_zscore
        self.exit_zscore = config.execution.exit_zscore
        self.stop_multiple = config.execution.stop_multiple
        self.volatility_window = config.execution.volatility_window
        self.prices: deque[float] = deque(maxlen=self.long_window)
        self.position = 0
        self.entry_price: float | None = None
        self.signals: list[str] = []

    async def on_order_book(self, snapshot: OrderBookSnapshot, broker: BrokerProtocol) -> None:
        if snapshot.symbol != self.config.symbol:
            return
        if not snapshot.levels:
            return
        best_bid = max(
            (level.price for level in snapshot.levels if level.side.value == "bid"), default=None
        )
        best_ask = min(
            (level.price for level in snapshot.levels if level.side.value == "ask"), default=None
        )
        if best_bid is None or best_ask is None:
            return

        mid = (best_bid + best_ask) / 2
        self.prices.append(mid)

        if len(self.prices) < self.long_window:
            return

        prices_list = list(self.prices)
        short_prices = prices_list[-self.short_window :]
        short_mean = fmean(short_prices)
        long_mean = fmean(prices_list)
        std_dev = pstdev(prices_list) or 1e-9
        zscore = (short_mean - long_mean) / std_dev

        # Stop-loss based on volatility window
        if self.entry_price is not None:
            vola_sample = prices_list[-min(self.volatility_window, len(prices_list)) :]
            vola = pstdev(vola_sample) or 1e-9
            if abs(mid - self.entry_price) >= self.stop_multiple * vola:
                await self._close_position(broker, mid, reason="stop")
                return

        if self.position == 0:
            if zscore <= -self.entry_zscore:
                await self._open_position(broker, OrderSide.BUY, mid)
                self.signals.append("enter_long")
            elif zscore >= self.entry_zscore:
                await self._open_position(broker, OrderSide.SELL, mid)
                self.signals.append("enter_short")
        else:
            if abs(zscore) <= self.exit_zscore:
                await self._close_position(broker, mid, reason="exit")

    async def on_fill(self, side: OrderSide, quantity: int) -> None:
        if side == OrderSide.BUY:
            self.position += quantity
        else:
            self.position -= quantity

    async def _open_position(self, broker: BrokerProtocol, side: OrderSide, price: float) -> None:
        order = OrderRequest(
            contract=SymbolContract(symbol=self.config.symbol),
            side=side,
            quantity=1,
            order_type=OrderType.LIMIT,
            limit_price=price,
        )
        await broker.submit_limit_order(order)
        self.entry_price = price

    async def _close_position(self, broker: BrokerProtocol, price: float, reason: str) -> None:
        if self.position == 0:
            return
        side = OrderSide.SELL if self.position > 0 else OrderSide.BUY
        order = OrderRequest(
            contract=SymbolContract(symbol=self.config.symbol),
            side=side,
            quantity=abs(self.position),
            order_type=OrderType.LIMIT,
            limit_price=price,
        )
        await broker.submit_limit_order(order)
        self.signals.append(f"exit_{reason}")
        self.position = 0
        self.entry_price = None


class SkewArbitrageStrategy(ReplayStrategy):
    """Detects skew opportunities across option surface snapshots."""

    def __init__(self, config: SkewArbitrageConfig) -> None:
        self.config = config
        self.opportunities: list[dict[str, float]] = []

    async def on_option_surface(self, entry: OptionSurfaceEntry, broker: BrokerProtocol) -> None:
        if entry.symbol != self.config.symbol:
            return
        if self.config.execution.expiries and entry.expiry not in self.config.execution.expiries:
            return
        skew = abs((entry.ask - entry.bid) / max(entry.mid or 1e-9, 1e-9))
        if skew >= self.config.execution.skew_threshold:
            self.opportunities.append(
                {
                    "expiry": entry.expiry,
                    "strike": entry.strike,
                    "right": entry.right.value,
                    "skew": skew,
                }
            )


class MicrostructureMLStrategy(ReplayStrategy):
    """Simple microstructure model driven by order book imbalance."""

    def __init__(self, config: MicrostructureMLConfig) -> None:
        self.config = config
        self.predictions: list[float] = []
        self.signals: list[str] = []

    async def on_order_book(self, snapshot: OrderBookSnapshot, broker: BrokerProtocol) -> None:
        if snapshot.symbol != self.config.symbol:
            return
        bid_volume = sum(level.size for level in snapshot.levels if level.side.value == "bid")
        ask_volume = sum(level.size for level in snapshot.levels if level.side.value == "ask")
        total_volume = max(bid_volume + ask_volume, 1e-9)
        imbalance = (bid_volume - ask_volume) / total_volume
        self.predictions.append(imbalance)
        if abs(imbalance) >= self.config.execution.confidence_threshold:
            signal = "buy" if imbalance > 0 else "sell"
            self.signals.append(signal)


class RegimeRotationStrategy(ReplayStrategy):
    """Regime rotation based on realized volatility proxy."""

    def __init__(self, config: RegimeRotationConfig) -> None:
        self.config = config
        self.returns: deque[float] = deque(maxlen=config.execution.regime_window)
        self.current_regime: str | None = None
        self.regime_history: list[str] = []

    async def on_order_book(self, snapshot: OrderBookSnapshot, broker: BrokerProtocol) -> None:
        if snapshot.symbol != self.config.symbol:
            return
        if not snapshot.levels:
            return
        mid = sum(level.price for level in snapshot.levels if level.side.value == "bid")
        mid += sum(level.price for level in snapshot.levels if level.side.value == "ask")
        mid /= max(len(snapshot.levels), 1)
        if hasattr(self, "_last_mid"):
            ret = (mid - self._last_mid) / max(self._last_mid, 1e-9)
            self.returns.append(ret)
            if len(self.returns) == self.returns.maxlen:
                vol = pstdev(self.returns) if len(self.returns) > 1 else 0.0
                regime = "high_vol" if vol > 0.02 else "low_vol"
                if regime != self.current_regime:
                    self.current_regime = regime
                    self.regime_history.append(regime)
        self._last_mid = mid


class VolSpilloverStrategy(ReplayStrategy):
    """Tracks volatility spillover across asset pairs."""

    def __init__(self, config: VolSpilloverConfig) -> None:
        self.config = config
        self.price_history: dict[str, deque[float]] = {
            symbol: deque(maxlen=config.execution.correlation_window)
            for pair in config.execution.asset_pairs
            for symbol in pair
        }
        self.alerts: list[tuple[list[str], float]] = []

    async def on_order_book(self, snapshot: OrderBookSnapshot, broker: BrokerProtocol) -> None:
        if snapshot.symbol not in self.price_history:
            return
        if not snapshot.levels:
            return
        mid = sum(level.price for level in snapshot.levels) / len(snapshot.levels)
        self.price_history[snapshot.symbol].append(mid)
        self._evaluate_pairs()

    async def on_trade(self, trade: TradeEvent, broker: BrokerProtocol) -> None:
        if trade.symbol in self.price_history:
            self.price_history[trade.symbol].append(trade.price)
            self._evaluate_pairs()

    def _evaluate_pairs(self) -> None:
        for pair in self.config.execution.asset_pairs:
            if any(len(self.price_history[s]) < 2 for s in pair):
                continue
            returns = []
            for symbol in pair:
                prices = list(self.price_history[symbol])
                ret = (prices[-1] - prices[0]) / max(prices[0], 1e-9)
                returns.append(ret)
            spread = abs(returns[0] - returns[1])
            if spread >= self.config.execution.spillover_threshold:
                self.alerts.append((pair, spread))


class VolatilityOverlayStrategy(ReplayStrategy):
    """Volatility-targeted directional overlay using simple conviction signals."""

    def __init__(self, config: VolatilityOverlayConfig) -> None:
        self.config = config
        self.prices: deque[float] = deque(maxlen=config.execution.lookback_window)
        self.returns: deque[float] = deque(maxlen=max(2, config.execution.lookback_window))
        self.position = 0
        self.target_history: list[int] = []

    async def on_order_book(self, snapshot: OrderBookSnapshot, broker: BrokerProtocol) -> None:
        if snapshot.symbol != self.config.symbol:
            return
        if not snapshot.levels:
            return
        mid = sum(level.price for level in snapshot.levels) / len(snapshot.levels)
        if hasattr(self, "_last_mid"):
            ret = (mid - self._last_mid) / max(self._last_mid, 1e-9)
            self.returns.append(ret)
        self._last_mid = mid
        self.prices.append(mid)

        if len(self.prices) < self.prices.maxlen:
            return

        direction = self._conviction()
        target_units = self._target_units()
        desired_position = direction * target_units
        self.target_history.append(desired_position)

        delta = desired_position - self.position
        if delta == 0:
            return

        side = OrderSide.BUY if delta > 0 else OrderSide.SELL
        order = OrderRequest(
            contract=SymbolContract(symbol=self.config.symbol),
            side=side,
            quantity=abs(delta),
            order_type=OrderType.LIMIT,
            limit_price=mid,
        )
        await broker.submit_limit_order(order)

    async def on_fill(self, side: OrderSide, quantity: int) -> None:
        self.position += quantity if side == OrderSide.BUY else -quantity

    def _conviction(self) -> int:
        execution = self.config.execution
        if execution.conviction_signal == "ma_cross":
            prices = list(self.prices)
            fast = max(2, execution.lookback_window // 4)
            slow = max(fast + 1, execution.lookback_window)
            fast_ma = fmean(prices[-fast:])
            slow_ma = fmean(prices[-slow:])
            return 1 if fast_ma > slow_ma else -1
        # default: price above mean => long
        mean_price = fmean(self.prices)
        return 1 if self.prices[-1] >= mean_price else -1

    def _target_units(self) -> int:
        execution = self.config.execution
        target_vol = execution.volatility_target or 0.1
        if len(self.returns) < 2:
            return 1
        realized_vol = pstdev(self.returns) or 1e-9
        raw_units = target_vol / realized_vol
        capped = min(raw_units, execution.leverage_cap or raw_units)
        return max(1, int(round(capped)))
