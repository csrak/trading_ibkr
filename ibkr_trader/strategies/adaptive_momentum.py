"""Adaptive momentum / mean-reversion hybrid strategy."""

from __future__ import annotations

from collections import deque
from datetime import datetime
from decimal import ROUND_DOWN, Decimal

from loguru import logger

from ibkr_trader.base_strategy import BrokerProtocol
from ibkr_trader.core import EventBus
from ibkr_trader.data import Screener
from ibkr_trader.risk import RiskGuard
from ibkr_trader.strategy import Strategy
from ibkr_trader.telemetry import TelemetryReporter

from .config import AdaptiveMomentumConfig
from .factors import MomentumReading, atr, momentum_signal


class AdaptiveMomentumStrategy(Strategy):
    """Adaptive momentum strategy scaffolding.

    Implements factor tracking and publishes telemetry; execution logic will be refined
    in follow-up iterations.
    """

    def __init__(
        self,
        config: AdaptiveMomentumConfig,
        broker: BrokerProtocol,
        event_bus: EventBus,
        risk_guard: RiskGuard | None = None,
        telemetry: TelemetryReporter | None = None,
    ) -> None:
        super().__init__(config=config, broker=broker, event_bus=event_bus, risk_guard=risk_guard)
        self.config = config
        self._telemetry = telemetry
        maxlen = max(config.slow_lookback * 4, 200)
        self._history_maxlen = maxlen
        self._price_history: dict[str, deque[Decimal]] = {
            symbol: deque(maxlen=maxlen) for symbol in self.config.symbols
        }
        self._high_history: dict[str, deque[Decimal]] = {
            symbol: deque(maxlen=maxlen) for symbol in self.config.symbols
        }
        self._screener: Screener | None = None
        self._last_screen_update: datetime | None = None
        self._low_history: dict[str, deque[Decimal]] = {
            symbol: deque(maxlen=maxlen) for symbol in self.config.symbols
        }

    async def on_bar(self, symbol: str, price: Decimal, broker: BrokerProtocol) -> None:
        history = self._price_history[symbol]
        history.append(price)
        # Placeholder highs/lows; will be replaced with OHLC feed integration.
        self._high_history[symbol].append(price)
        self._low_history[symbol].append(price)

        momentum = momentum_signal(history, self.config.fast_lookback, self.config.slow_lookback)
        volatility = atr(
            prices=history,
            highs=self._high_history[symbol],
            lows=self._low_history[symbol],
            period=self.config.atr_lookback,
        )

        if len(history) < self.config.slow_lookback:
            self._emit_signal_snapshot(symbol, price, momentum, volatility, reason="warmup")
            return

        edge_bps = self._estimate_edge(momentum, volatility)
        if edge_bps < self.config.min_edge_bps:
            self._emit_signal_snapshot(
                symbol,
                price,
                momentum,
                volatility,
                reason=f"edge_below_threshold({edge_bps:.2f})",
            )
            return

        target_qty = await self._compute_target_quantity(symbol, momentum, volatility)
        await self.submit_target_position(
            symbol,
            target_qty,
            metadata={
                "expected_edge_bps": float(edge_bps),
                "volatility": float(volatility),
                "momentum": float(momentum.signal),
            },
        )
        self._emit_signal_snapshot(symbol, price, momentum, volatility, reason="submitted")

    def _estimate_edge(self, momentum: MomentumReading, volatility: Decimal) -> Decimal:
        if volatility <= 0:
            return Decimal("0")
        signal_strength = momentum.signal
        return (signal_strength / volatility) * Decimal("10000")

    async def _compute_target_quantity(
        self, symbol: str, momentum: MomentumReading, volatility: Decimal
    ) -> int:
        if volatility <= 0:
            return 0
        account_equity = Decimal("1")  # Placeholder until connected to broker summary.
        risk_budget = account_equity * self.config.max_risk_fraction
        if risk_budget <= 0:
            return 0
        notional_per_share = max(volatility, Decimal("0.01"))
        max_shares = int((risk_budget / notional_per_share).to_integral_value(rounding=ROUND_DOWN))
        direction = 1 if momentum.signal > 0 else -1
        return direction * min(max_shares, self.config.position_size)

    def _emit_signal_snapshot(
        self,
        symbol: str,
        price: Decimal,
        momentum: MomentumReading,
        volatility: Decimal,
        reason: str,
    ) -> None:
        if self._telemetry is None:
            logger.debug(
                "Signal snapshot {} price={} momentum={} volatility={}",
                symbol,
                price,
                momentum.signal,
                volatility,
            )
            return

        self._telemetry.info(
            f"{self.config.telemetry_namespace}.signal_snapshot",
            context={
                "symbol": symbol,
                "price": float(price),
                "momentum": float(momentum.signal),
                "fast_mean": float(momentum.fast_mean),
                "slow_mean": float(momentum.slow_mean),
                "volatility": float(volatility),
                "reason": reason,
            },
        )

    def set_screener(self, screener: Screener | None) -> None:
        self._screener = screener

    async def refresh_universe(self) -> None:
        if self._screener is None:
            return
        result = await self._screener.run()
        new_symbols = {symbol.upper() for symbol in result.symbols}
        if not new_symbols:
            logger.warning("Screener returned empty universe; retaining previous symbols")
            return
        self._symbols = new_symbols

        def _ensure_buffers(store: dict[str, deque[Decimal]]) -> dict[str, deque[Decimal]]:
            updated: dict[str, deque[Decimal]] = {}
            for sym in new_symbols:
                updated[sym] = store.get(sym, deque(maxlen=self._history_maxlen))
            return updated

        self._price_history = _ensure_buffers(self._price_history)
        self._high_history = _ensure_buffers(self._high_history)
        self._low_history = _ensure_buffers(self._low_history)
        self._last_screen_update = result.generated_at
        if self._telemetry is not None:
            self._telemetry.info(
                f"{self.config.telemetry_namespace}.screen_refresh",
                context={
                    "symbols": list(new_symbols),
                    "generated_at": result.generated_at.isoformat(),
                    "metadata": result.metadata or {},
                },
            )
