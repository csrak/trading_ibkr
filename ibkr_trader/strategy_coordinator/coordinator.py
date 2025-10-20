"""StrategyCoordinator orchestrates multiple strategies sharing a broker instance."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Mapping
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from loguru import logger

from ibkr_trader.base_strategy import BrokerProtocol
from ibkr_trader.events import EventBus
from ibkr_trader.market_data import MarketDataService, SubscriptionRequest
from ibkr_trader.models import (
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderType,
    Position,
    SymbolContract,
)
from ibkr_trader.order_intents import MARKET_DELTA, TARGET_POSITION, OrderIntent
from ibkr_trader.portfolio import RiskGuard
from ibkr_trader.strategy_configs.graph import StrategyGraphConfig, StrategyNodeConfig
from ibkr_trader.telemetry import TelemetryReporter

from .errors import CapitalAllocationError, StrategyInitializationError
from .policies import CapitalAllocationPolicy, EqualWeightPolicy, PositionEnvelope
from .wrapper import StrategyWrapper, build_strategy_wrapper


@dataclass(slots=True)
class CoordinatorContext:
    """Runtime bookkeeping for a strategy managed by the coordinator."""

    wrapper: StrategyWrapper
    proxy: CoordinatorBrokerProxy
    last_notional: Decimal | None = None


class CoordinatorBrokerProxy(BrokerProtocol):
    """Broker proxy enforcing coordinator capital envelopes before routing orders."""

    def __init__(
        self,
        *,
        strategy_id: str,
        base_broker: BrokerProtocol,
        policy: CapitalAllocationPolicy,
        risk_guard: RiskGuard | None,
        telemetry: TelemetryReporter | None,
        exposure_hook: Callable[[str, str, Decimal], None] | None,
    ) -> None:
        self._strategy_id = strategy_id
        self._broker = base_broker
        self._policy = policy
        self._risk_guard = risk_guard
        self._telemetry = telemetry
        self._exposure_hook = exposure_hook

    async def place_order(self, request: OrderRequest) -> OrderResult:
        symbol = request.contract.symbol
        envelope = self._policy.envelope_for(self._strategy_id, symbol)
        adjusted_request = self._apply_envelope(request, envelope)

        price = self._resolve_price(adjusted_request)
        if self._risk_guard is not None:
            await self._risk_guard.validate_order(
                adjusted_request.contract,
                adjusted_request.side,
                adjusted_request.quantity,
                price,
            )

        signed_notional = Decimal("0")
        if price > 0:
            signed_notional = price * Decimal(
                adjusted_request.quantity
                if adjusted_request.side == OrderSide.BUY
                else -adjusted_request.quantity
            )
            if self._exposure_hook is not None:
                self._exposure_hook(self._strategy_id, symbol, signed_notional)
            self._record_exposure_event(symbol, price, adjusted_request.quantity)

        return await self._broker.place_order(adjusted_request)

    async def get_positions(self) -> list[Position]:
        return await self._broker.get_positions()

    def _apply_envelope(self, request: OrderRequest, envelope: PositionEnvelope) -> OrderRequest:
        quantity = request.quantity
        symbol = request.contract.symbol
        original_quantity = quantity
        max_position = envelope.max_position
        max_notional = envelope.max_notional

        if max_position is not None and quantity > max_position:
            logger.warning(
                "Coordinator clipped order quantity from {} to {} for strategy {}",
                quantity,
                max_position,
                self._strategy_id,
            )
            self._emit_clip_event(
                symbol,
                "max_position",
                quantity,
                max_position,
                extra={"max_position": max_position},
            )
            quantity = max_position

        if max_notional is not None:
            price = request.expected_price
            if price is not None and price > 0:
                allowed_decimal: Decimal = (max_notional / price).to_integral_value(
                    rounding=ROUND_DOWN
                )
                allowed = int(allowed_decimal)
                if allowed == 0:
                    self._emit_clip_event(
                        symbol,
                        "notional_zero",
                        quantity,
                        0,
                        extra={
                            "max_notional": str(max_notional),
                            "price": str(price),
                        },
                    )
                    raise CapitalAllocationError(
                        f"Order would exceed notional cap {max_notional} "
                        f"for strategy {self._strategy_id}"
                    )
                if quantity > allowed:
                    logger.warning(
                        (
                            "Coordinator clipped order quantity from {} to {} "
                            "for strategy {} due to notional cap"
                        ),
                        quantity,
                        allowed,
                        self._strategy_id,
                    )
                    self._emit_clip_event(
                        symbol,
                        "max_notional",
                        quantity,
                        allowed,
                        extra={
                            "max_notional": str(max_notional),
                            "price": str(price),
                        },
                    )
                    quantity = allowed

        if quantity <= 0:
            self._emit_clip_event(
                symbol,
                "non_positive_quantity",
                original_quantity,
                quantity,
                extra=None,
            )
            raise CapitalAllocationError(
                f"Order rejected: quantity clipped to <= 0 for strategy {self._strategy_id}"
            )

        if quantity == request.quantity:
            return request

        return request.model_copy(update={"quantity": quantity})

    def _resolve_price(self, request: OrderRequest) -> Decimal:
        price = request.expected_price
        if price is None:
            price = request.limit_price
        if price is None:
            price = request.stop_price
        if price is None:
            return Decimal("0")
        return Decimal(str(price))

    def _emit_clip_event(
        self,
        symbol: str,
        reason: str,
        original_quantity: int,
        adjusted_quantity: int,
        *,
        extra: dict[str, object] | None,
    ) -> None:
        if self._telemetry is None:
            return
        context: dict[str, object] = {
            "strategy_id": self._strategy_id,
            "symbol": symbol,
            "reason": reason,
            "original_quantity": original_quantity,
            "adjusted_quantity": adjusted_quantity,
        }
        if extra:
            context.update(extra)
        self._telemetry.warning("coordinator.order_clipped", context=context)

    def _record_exposure_event(self, symbol: str, price: Decimal, quantity: int) -> None:
        if self._telemetry is None:
            return
        self._telemetry.info(
            "coordinator.order_allocation",
            context={
                "strategy_id": self._strategy_id,
                "symbol": symbol,
                "price": float(price),
                "quantity": quantity,
                "notional": float(price * Decimal(quantity)),
            },
        )


class StrategyCoordinator:
    """Manage lifecycle and capital allocation for multiple strategies."""

    def __init__(
        self,
        *,
        broker: BrokerProtocol,
        event_bus: EventBus,
        market_data: MarketDataService,
        risk_guard: RiskGuard | None,
        capital_policy: CapitalAllocationPolicy | None = None,
        telemetry: TelemetryReporter | None = None,
        subscribe_market_data: bool = True,
    ) -> None:
        self._broker = broker
        self._event_bus = event_bus
        self._market_data = market_data
        self._risk_guard = risk_guard
        self._capital_policy = capital_policy
        self._telemetry = telemetry
        self._exposure: dict[tuple[str, str], Decimal] = {}
        self._positions: dict[str, Decimal] = {}
        self._total_notional: Decimal = Decimal("0")
        self._enable_live_subscriptions = subscribe_market_data
        self._intent_queue: asyncio.Queue[OrderIntent] = asyncio.Queue()
        self._intent_task: asyncio.Task[None] | None = None
        self._policy: CapitalAllocationPolicy | None = None
        self._graph: StrategyGraphConfig | None = None
        self._contexts: dict[str, CoordinatorContext] = {}
        self._subscriptions: AsyncExitStack | None = None
        self._lock = asyncio.Lock()

    async def start(self, graph: StrategyGraphConfig) -> None:
        """Start all strategies defined in graph."""
        async with self._lock:
            if self._graph is not None:
                raise RuntimeError("StrategyCoordinator already running")

            policy = self._capital_policy or EqualWeightPolicy(graph.capital_policy)
            policy.prepare(graph)
            self._policy = policy

            contexts: dict[str, CoordinatorContext] = {}
            for node in graph.strategies:
                try:
                    proxy = CoordinatorBrokerProxy(
                        strategy_id=node.id,
                        base_broker=self._broker,
                        policy=policy,
                        risk_guard=self._risk_guard,
                        telemetry=self._telemetry,
                        exposure_hook=self._record_exposure,
                    )
                    wrapper = build_strategy_wrapper(
                        node=node,
                        broker=proxy,
                        event_bus=self._event_bus,
                        risk_guard=self._risk_guard,
                    )
                    wrapper.impl.set_coordinator_identity(node.id)
                    wrapper.impl.set_order_intent_queue(self._intent_queue)
                except Exception as exc:
                    raise StrategyInitializationError(
                        f"Failed to initialize strategy '{node.id}': {exc}"
                    ) from exc
                contexts[node.id] = CoordinatorContext(wrapper=wrapper, proxy=proxy)

            subscription_stack = AsyncExitStack()
            if self._enable_live_subscriptions:
                await self._subscribe_market_data(subscription_stack, graph.strategies)

            for context in contexts.values():
                await context.wrapper.start()

            self._contexts = contexts
            self._graph = graph
            self._subscriptions = subscription_stack
            logger.info("StrategyCoordinator started with %d strategies", len(contexts))

            if self._intent_task is None:
                self._intent_task = asyncio.create_task(self._run_intent_loop())

    async def stop(self) -> None:
        """Stop all strategies and release resources."""
        async with self._lock:
            contexts = list(self._contexts.values())
            self._contexts.clear()
            self._graph = None
            self._policy = None
            subscription_stack = self._subscriptions
            self._subscriptions = None

        for context in contexts:
            context.wrapper.impl.set_order_intent_queue(None)
            await context.wrapper.stop()

        if subscription_stack is not None:
            await subscription_stack.aclose()

        if self._intent_task is not None:
            self._intent_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._intent_task
            self._intent_task = None

        while not self._intent_queue.empty():
            try:
                self._intent_queue.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover - defensive
                break
            else:
                self._intent_queue.task_done()

        self._positions.clear()
        self._total_notional = Decimal("0")

        logger.info("StrategyCoordinator stopped")

    def _record_exposure(self, strategy_id: str, symbol: str, signed_notional: Decimal) -> None:
        self._exposure[(strategy_id, symbol)] = signed_notional
        context = self._contexts.get(strategy_id)
        if context is not None:
            context.last_notional = signed_notional
        self._positions[symbol] = self._positions.get(symbol, Decimal("0")) + signed_notional
        self._total_notional = sum(abs(val) for val in self._positions.values())
        if self._telemetry is not None:
            self._telemetry.info(
                "coordinator.exposure_snapshot",
                context={
                    "strategy_id": strategy_id,
                    "symbol": symbol,
                    "notional": float(signed_notional),
                    "total_notional": float(self._total_notional),
                },
            )

    async def _subscribe_market_data(
        self,
        stack: AsyncExitStack,
        nodes: Iterable[StrategyNodeConfig],
    ) -> None:
        unique_symbols: set[str] = set()
        for node in nodes:
            unique_symbols.update(node.symbols)

        for symbol in sorted(unique_symbols):
            request = SubscriptionRequest(contract=SymbolContract(symbol=symbol))
            context = self._market_data.subscribe(request)
            await stack.enter_async_context(context)

    @property
    def strategies(self) -> Mapping[str, StrategyWrapper]:
        return {strategy_id: context.wrapper for strategy_id, context in self._contexts.items()}

    async def _run_intent_loop(self) -> None:
        while True:
            intent = await self._intent_queue.get()
            try:
                await self._handle_intent(intent)
            except asyncio.CancelledError:  # pragma: no cover - task cancelled during shutdown
                raise
            except Exception as exc:  # pragma: no cover - unexpected runtime error
                logger.error("Failed to process order intent {}: {}", intent, exc)
                if self._telemetry is not None:
                    self._telemetry.error(
                        "coordinator.intent_error",
                        context={
                            "strategy_id": intent.strategy_id,
                            "symbol": intent.symbol,
                            "intent_type": intent.intent_type,
                            "error": str(exc),
                        },
                    )
            finally:
                self._intent_queue.task_done()

    async def _handle_intent(self, intent: OrderIntent) -> None:
        context = self._contexts.get(intent.strategy_id)
        if context is None:
            logger.warning("Received intent for unknown strategy {}", intent.strategy_id)
            return

        if self._telemetry is not None:
            self._telemetry.info(
                "coordinator.intent_received",
                context={
                    "strategy_id": intent.strategy_id,
                    "symbol": intent.symbol,
                    "intent_type": intent.intent_type,
                    "quantity": intent.quantity,
                },
            )

        if intent.intent_type == TARGET_POSITION:
            current_position = await self._get_current_position(intent.symbol)
            delta = intent.quantity - current_position
        elif intent.intent_type == MARKET_DELTA:
            delta = intent.quantity
        else:
            logger.warning("Unsupported intent type '{}'", intent.intent_type)
            return

        if delta == 0:
            if self._telemetry is not None:
                self._telemetry.info(
                    "coordinator.intent_ignored",
                    context={
                        "strategy_id": intent.strategy_id,
                        "symbol": intent.symbol,
                        "intent_type": intent.intent_type,
                        "reason": "delta_zero",
                    },
                )
            return

        side = OrderSide.BUY if delta > 0 else OrderSide.SELL
        quantity = abs(delta)
        price = self._resolve_price_from_strategy(context.wrapper, intent.symbol)
        contract = SymbolContract(symbol=intent.symbol)
        effective_price = price or Decimal("0")

        if self._risk_guard is not None and effective_price > 0:
            await self._risk_guard.validate_order(contract, side, quantity, effective_price)

        order_request = OrderRequest(
            contract=contract,
            side=side,
            quantity=quantity,
            order_type=OrderType.MARKET,
            expected_price=effective_price if effective_price > 0 else None,
        )

        result = await context.proxy.place_order(order_request)

        if self._telemetry is not None:
            self._telemetry.info(
                "coordinator.intent_fulfilled",
                context={
                    "strategy_id": intent.strategy_id,
                    "symbol": intent.symbol,
                    "intent_type": intent.intent_type,
                    "quantity": quantity,
                    "order_id": result.order_id,
                    "total_notional": float(self._total_notional),
                },
            )

    async def _get_current_position(self, symbol: str) -> int:
        positions = await self._broker.get_positions()
        for pos in positions:
            if pos.contract.symbol == symbol:
                return pos.quantity
        return 0

    @staticmethod
    def _resolve_price_from_strategy(wrapper: StrategyWrapper, symbol: str) -> Decimal | None:
        event = wrapper.impl.last_event()
        if event is None:
            return None
        try:
            return event.price if isinstance(event.price, Decimal) else Decimal(str(event.price))
        except Exception:  # pragma: no cover - defensive conversion
            logger.debug("Unable to resolve price from last event for {}", symbol)
            return None
