"""Kill switch management for automated safety halts."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from threading import Lock
from typing import Any

from loguru import logger

from ibkr_trader.core.alerting import AlertMessage


@dataclass(slots=True)
class KillSwitchState:
    engaged: bool = False
    triggered_at: str | None = None
    alert_title: str | None = None
    alert_message: str | None = None
    severity: str | None = None
    context: dict[str, Any] | None = None
    acknowledged: bool = False
    acknowledged_by: str | None = None
    acknowledged_at: str | None = None
    note: str | None = None


class KillSwitch:
    """Manages kill switch state with persistence."""

    def __init__(
        self,
        state_file: Path,
        *,
        cancel_orders_enabled: bool = True,
    ) -> None:
        self._path = state_file
        self._lock = Lock()
        self._state = self._load()
        self._cancel_orders_enabled = cancel_orders_enabled

    def engage(self, alert: AlertMessage) -> bool:
        """Engage the kill switch based on alert details.

        Returns True if the switch transitioned to engaged state, False otherwise.
        """
        with self._lock:
            if self._state.engaged:
                return False
            self._state = KillSwitchState(
                engaged=True,
                triggered_at=alert.timestamp.isoformat(),
                alert_title=alert.title,
                alert_message=alert.message,
                severity=alert.severity.value,
                context=alert.context or {},
                acknowledged=False,
                acknowledged_by=None,
                acknowledged_at=None,
                note=None,
            )
            self._save()
            logger.critical("Kill switch engaged due to alert: {} - {}", alert.title, alert.message)
            return True

    def clear(self, *, acknowledged_by: str, note: str | None = None) -> bool:
        """Clear the kill switch after operator acknowledgement."""
        with self._lock:
            if not self._state.engaged:
                return False
            now = datetime.now(tz=UTC).isoformat()
            self._state.acknowledged = True
            self._state.acknowledged_by = acknowledged_by
            self._state.acknowledged_at = now
            self._state.note = note
            self._state.engaged = False
            self._save()
            logger.info(
                "Kill switch cleared by {} (note: {})",
                acknowledged_by,
                note or "none",
            )
            return True

    def is_engaged(self) -> bool:
        with self._lock:
            return self._state.engaged

    def status(self) -> KillSwitchState:
        with self._lock:
            return KillSwitchState(**asdict(self._state))

    @property
    def cancel_orders_enabled(self) -> bool:
        return self._cancel_orders_enabled

    def _load(self) -> KillSwitchState:
        if not self._path.exists():
            return KillSwitchState()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return KillSwitchState(
                engaged=bool(data.get("engaged", False)),
                triggered_at=data.get("triggered_at"),
                alert_title=data.get("alert_title"),
                alert_message=data.get("alert_message"),
                severity=data.get("severity"),
                context=data.get("context") or {},
                acknowledged=bool(data.get("acknowledged", False)),
                acknowledged_by=data.get("acknowledged_by"),
                acknowledged_at=data.get("acknowledged_at"),
                note=data.get("note"),
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to load kill switch state: {}", exc)
            return KillSwitchState()

    def _save(self) -> None:
        payload = self._sanitize(asdict(self._state))
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Failed to persist kill switch state: {}", exc)

    @staticmethod
    def _sanitize(value: object) -> object:
        if value is None:
            return None
        if isinstance(value, dict):
            return {str(k): KillSwitch._sanitize(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [KillSwitch._sanitize(v) for v in value]
        if isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Decimal):
            return float(value)
        return str(value)
