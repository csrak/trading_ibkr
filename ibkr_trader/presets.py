"""Trade presets for quick paper-order testing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable

from ibkr_trader.models import SymbolContract


@dataclass(frozen=True, slots=True)
class TradePreset:
    """Preset defining a contract and default quantity."""

    contract: SymbolContract
    default_quantity: int

    def with_quantity(self, quantity: int | None) -> tuple[SymbolContract, int]:
        """Return contract and quantity, falling back to default when None."""
        qty = quantity if quantity is not None else self.default_quantity
        return self.contract, qty


_PRESETS: Dict[str, TradePreset] = {
    "eurusd": TradePreset(
        # TODO: Confirm FX preset once account permissions allow leverage-less conversions.
        contract=SymbolContract(
            symbol="EUR",
            sec_type="CASH",
            exchange="IDEALPRO",
            currency="USD",
        ),
        default_quantity=10_000,
    ),
    "gbpusd": TradePreset(
        # TODO: Confirm FX preset once account permissions allow leverage-less conversions.
        contract=SymbolContract(
            symbol="GBP",
            sec_type="CASH",
            exchange="IDEALPRO",
            currency="USD",
        ),
        default_quantity=10_000,
    ),
    "spy": TradePreset(
        contract=SymbolContract(
            symbol="SPY",
            sec_type="STK",
            exchange="SMART",
            currency="USD",
        ),
        default_quantity=1,
    ),
    "qqq": TradePreset(
        contract=SymbolContract(
            symbol="QQQ",
            sec_type="STK",
            exchange="SMART",
            currency="USD",
        ),
        default_quantity=1,
    ),
}


def get_preset(name: str) -> TradePreset:
    """Return preset by name, case-insensitive.

    Raises:
        KeyError: If preset is unknown.
    """
    key = name.lower()
    if key not in _PRESETS:
        raise KeyError(name)
    return _PRESETS[key]


def preset_names() -> Iterable[str]:
    """Return available preset names."""
    return _PRESETS.keys()
