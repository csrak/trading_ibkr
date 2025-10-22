"""Alerting helpers that bridge telemetry into the on-call stack."""

from __future__ import annotations

import asyncio
import contextlib
import json
import ssl
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol
from urllib import request
from urllib.error import URLError

from loguru import logger

from ibkr_trader.core.events import DiagnosticEvent, EventBus, EventSubscription, EventTopic

if TYPE_CHECKING:  # pragma: no cover - type checking only
    from ibkr_trader.core.kill_switch import KillSwitch


class AlertSeverity(str, Enum):
    """Alert severity levels."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(slots=True)
class AlertMessage:
    """Structured message sent to the alerting transport."""

    severity: AlertSeverity
    title: str
    message: str
    timestamp: datetime
    context: dict[str, object]


class AlertTransport(Protocol):
    """Transport interface for alert delivery."""

    def send(self, alert: AlertMessage) -> None | Awaitable[None]: ...


class LogAlertTransport:
    """Fallback transport that logs alerts locally."""

    def send(self, alert: AlertMessage) -> None:
        logger.log(
            alert.severity.value.upper(),
            "[alert] {title} :: {message} | {context}",
            title=alert.title,
            message=alert.message,
            context=alert.context,
        )


class WebhookAlertTransport:
    """POST alerts to a central webhook receiver."""

    def __init__(self, url: str, *, timeout: float = 5.0, verify_ssl: bool = True) -> None:
        self._url = url
        self._timeout = timeout
        self._ssl_context = (
            ssl.create_default_context() if verify_ssl else ssl._create_unverified_context()
        )  # type: ignore[attr-defined]

    def send(self, alert: AlertMessage) -> None:
        payload = json.dumps(
            {
                "severity": alert.severity.value,
                "title": alert.title,
                "message": alert.message,
                "timestamp": alert.timestamp.isoformat(),
                "context": alert.context,
            }
        ).encode("utf-8")
        req = request.Request(
            self._url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self._timeout, context=self._ssl_context) as response:
                response.read()  # Drain response body to avoid resource warnings.
        except URLError as exc:  # pragma: no cover - network/environment dependent
            logger.warning("Failed to deliver alert to webhook {}: {}", self._url, exc)


@dataclass(slots=True)
class TelemetryAlertConfig:
    """Configuration thresholds for telemetry-to-alert routing."""

    trailing_rate_limit_threshold: int = 5
    trailing_rate_limit_window: timedelta = timedelta(seconds=60)
    trailing_rate_limit_cooldown: timedelta = timedelta(seconds=120)
    screener_stale_after: timedelta = timedelta(seconds=900)
    screener_check_interval: timedelta = timedelta(seconds=60)


class TelemetryAlertRouter:
    """Listens to telemetry events and emits alerts for operational responders."""

    def __init__(
        self,
        event_bus: EventBus,
        transport: AlertTransport,
        config: TelemetryAlertConfig | None = None,
        *,
        kill_switch: KillSwitch | None = None,
        on_kill: Callable[[AlertMessage], None] | None = None,
        extra_context: dict[str, object] | None = None,
        history_path: Path | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._transport = transport
        self._config = config or TelemetryAlertConfig()
        self._subscription: EventSubscription | None = None
        self._consumer_task: asyncio.Task[None] | None = None
        self._staleness_task: asyncio.Task[None] | None = None
        self._rate_limit_events: dict[str, deque[datetime]] = defaultdict(deque)
        self._rate_limit_alerted_at: dict[str, datetime] = {}
        self._screener_last_refresh: dict[str, datetime] = {}
        self._screener_alerted: set[str] = set()
        self._stopped = asyncio.Event()
        self._kill_switch = kill_switch
        self._on_kill = on_kill
        self._extra_context = dict(extra_context or {})
        self._history_path = history_path

    async def start(self) -> None:
        """Begin processing telemetry events."""
        self._stopped = asyncio.Event()
        self._rate_limit_events.clear()
        self._rate_limit_alerted_at.clear()
        self._screener_last_refresh.clear()
        self._screener_alerted.clear()
        self._subscription = self._event_bus.subscribe(EventTopic.DIAGNOSTIC)
        self._consumer_task = asyncio.create_task(self._consume())
        self._staleness_task = asyncio.create_task(self._monitor_staleness())

    async def stop(self) -> None:
        """Stop processing and release resources."""
        self._stopped.set()
        if self._subscription is not None:
            self._subscription.close()
        tasks = [task for task in (self._consumer_task, self._staleness_task) if task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(*tasks)
        self._consumer_task = None
        self._staleness_task = None
        self._subscription = None

    async def _consume(self) -> None:
        if self._subscription is None:
            return
        try:
            async for event in self._subscription:
                if isinstance(event, DiagnosticEvent):
                    self._handle_event(event)
        except asyncio.CancelledError:
            raise

    async def _monitor_staleness(self) -> None:
        """Periodically evaluate screener freshness and raise alerts."""
        interval = max(self._config.screener_check_interval.total_seconds(), 0.1)
        try:
            while not self._stopped.is_set():
                await asyncio.sleep(interval)
                self._evaluate_screener_staleness()
        except asyncio.CancelledError:
            raise

    def _handle_event(self, event: DiagnosticEvent) -> None:
        message = event.message
        if message.endswith(".screen_refresh"):
            namespace = message.removesuffix(".screen_refresh")
            self._screener_last_refresh[namespace] = event.timestamp
            if namespace in self._screener_alerted:
                self._dispatch(
                    AlertMessage(
                        severity=AlertSeverity.INFO,
                        title=f"Screener Resumed: {namespace}",
                        message="Universe refresh telemetry resumed within threshold.",
                        timestamp=datetime.now(tz=UTC),
                        context={"namespace": namespace},
                    )
                )
                self._screener_alerted.discard(namespace)
        elif message == "trailing_stop.rate_limited":
            context = event.context or {}
            stop_id = str(context.get("stop_id") or context.get("symbol") or "unknown")
            self._track_rate_limit(stop_id, event)

    def _track_rate_limit(self, key: str, event: DiagnosticEvent) -> None:
        window = self._config.trailing_rate_limit_window
        threshold = self._config.trailing_rate_limit_threshold
        cooldown = self._config.trailing_rate_limit_cooldown
        events = self._rate_limit_events[key]
        now = event.timestamp

        # Purge stale entries
        while events and now - events[0] > window:
            events.popleft()
        events.append(now)

        if len(events) < threshold:
            return

        last_alert = self._rate_limit_alerted_at.get(key)
        if last_alert is not None and now - last_alert < cooldown:
            return

        self._rate_limit_alerted_at[key] = now
        ctx = event.context or {}
        symbol = ctx.get("symbol") or "UNKNOWN"
        alert = AlertMessage(
            severity=AlertSeverity.WARNING,
            title=f"Trailing stop updates throttled for {symbol}",
            message=(
                "Trailing stop modifications were rate-limited "
                f"{len(events)} times within {int(window.total_seconds())}s."
            ),
            timestamp=datetime.now(tz=UTC),
            context={
                "stop_id": key,
                "symbol": symbol,
                "occurrences": len(events),
                "window_seconds": int(window.total_seconds()),
            },
        )
        self._dispatch(alert)

    def _evaluate_screener_staleness(self) -> None:
        stale_after = self._config.screener_stale_after
        if stale_after.total_seconds() <= 0:
            return

        now = datetime.now(tz=UTC)
        for namespace, last_refresh in list(self._screener_last_refresh.items()):
            delta = now - last_refresh
            if delta > stale_after:
                if namespace in self._screener_alerted:
                    continue
                alert = AlertMessage(
                    severity=AlertSeverity.CRITICAL,
                    title=f"Screener stalled: {namespace}",
                    message=(
                        f"No universe refresh telemetry received for {int(delta.total_seconds())}s "
                        f"(threshold {int(stale_after.total_seconds())}s)."
                    ),
                    timestamp=now,
                    context={
                        "namespace": namespace,
                        "last_refresh": last_refresh.isoformat(),
                        "stale_seconds": int(delta.total_seconds()),
                    },
                )
                self._dispatch(alert)
                self._screener_alerted.add(namespace)
            elif namespace in self._screener_alerted and delta <= stale_after:
                # Recovery is handled within _handle_event when new refresh arrives.
                self._screener_alerted.discard(namespace)

    def _dispatch(self, alert: AlertMessage) -> None:
        merged_context: dict[str, object] = dict(self._extra_context)
        if alert.context:
            merged_context.update(alert.context)
        enriched_alert = AlertMessage(
            severity=alert.severity,
            title=alert.title,
            message=alert.message,
            timestamp=alert.timestamp,
            context=merged_context,
        )

        result = self._transport.send(enriched_alert)
        if asyncio.iscoroutine(result):
            asyncio.create_task(result)  # pragma: no cover - transports may be async
        asyncio.create_task(self._event_bus.publish(EventTopic.ALERT, enriched_alert))
        if enriched_alert.severity == AlertSeverity.CRITICAL and self._kill_switch is not None:
            try:
                engaged = self._kill_switch.engage(enriched_alert)
            except Exception as exc:  # pragma: no cover - defensive
                logger.error("Failed to engage kill switch: {}", exc)
                engaged = False
            if engaged and self._on_kill is not None:
                try:
                    self._on_kill(enriched_alert)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.error("Kill callback failed: {}", exc)
        if self._history_path is not None:
            try:
                record = {
                    "severity": enriched_alert.severity.value,
                    "title": enriched_alert.title,
                    "message": enriched_alert.message,
                    "timestamp": enriched_alert.timestamp.isoformat(),
                    "context": self._sanitize_context(enriched_alert.context),
                }
                self._history_path.parent.mkdir(parents=True, exist_ok=True)
                with self._history_path.open("a", encoding="utf-8") as handle:
                    json.dump(record, handle, separators=(",", ":"))
                    handle.write("\n")
            except Exception as exc:  # pragma: no cover - defensive
                logger.error("Failed to append alert history: {}", exc)

    @staticmethod
    def _sanitize_context(context: dict[str, object]) -> dict[str, object]:
        def _sanitize(value: object) -> object:
            if value is None:
                return None
            if isinstance(value, dict):
                return {str(k): _sanitize(v) for k, v in value.items()}
            if isinstance(value, (list, tuple, set)):
                return [_sanitize(v) for v in value]
            if isinstance(value, (str, int, float, bool)):
                return value
            if isinstance(value, Decimal):
                return float(value)
            return str(value)

        return {str(k): _sanitize(v) for k, v in context.items()} if context else {}
