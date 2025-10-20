"""Advanced risk management utilities (correlation-based exposure limits)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from loguru import logger

from ibkr_trader.models import OrderSide, SymbolContract
from ibkr_trader.portfolio import PortfolioState


class CorrelationMatrix:
    """Lightweight correlation matrix helper.

    Stores pairwise correlation coefficients between symbols using a symmetric
    dictionary representation. Values are floats in the range [-1, 1].
    """

    def __init__(self, data: dict[str, dict[str, float]] | None = None) -> None:
        self._matrix: dict[str, dict[str, float]] = {}
        if data:
            for symbol, mapping in data.items():
                for other, value in mapping.items():
                    try:
                        self.set_correlation(symbol, other, float(value))
                    except (TypeError, ValueError):
                        logger.warning(
                            "Skipping invalid correlation entry %s -> %s=%s",
                            symbol,
                            other,
                            value,
                        )

    def set_correlation(self, symbol_a: str, symbol_b: str, value: float) -> None:
        """Set correlation coefficient between two symbols."""
        if not (-1.0 <= value <= 1.0):
            raise ValueError("Correlation coefficient must be between -1.0 and 1.0")

        a = symbol_a.upper()
        b = symbol_b.upper()
        self._matrix.setdefault(a, {})[b] = value
        self._matrix.setdefault(b, {})[a] = value
        # Ensure the diagonal entries exist for quick lookups
        self._matrix.setdefault(a, {}).setdefault(a, 1.0)
        self._matrix.setdefault(b, {}).setdefault(b, 1.0)

    def get_correlation(self, symbol_a: str, symbol_b: str) -> float | None:
        """Return correlation coefficient between two symbols, if known."""
        a = symbol_a.upper()
        b = symbol_b.upper()
        return self._matrix.get(a, {}).get(b)

    def get_correlated_symbols(self, symbol: str, threshold: float) -> list[str]:
        """Return symbols whose absolute correlation exceeds threshold."""
        symbol_upper = symbol.upper()
        correlations = self._matrix.get(symbol_upper, {})
        correlated: list[str] = []
        for other, value in correlations.items():
            if other == symbol_upper:
                continue
            if abs(value) >= threshold:
                correlated.append(other)
        return correlated

    def to_dict(self) -> dict[str, dict[str, float]]:
        """Serialize matrix to a JSON-friendly dict."""
        return {
            symbol: {other: float(value) for other, value in mapping.items() if symbol != other}
            for symbol, mapping in self._matrix.items()
        }

    def save(self, path: Path) -> None:
        """Persist correlation matrix to disk."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
            logger.info("Saved correlation matrix to %s", path)
        except Exception as exc:  # pragma: no cover - IO safety
            logger.error("Failed to save correlation matrix to %s: %s", path, exc)

    @classmethod
    def load(cls, path: Path) -> CorrelationMatrix | None:
        """Load correlation matrix from JSON file, if available."""
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Correlation matrix must be a JSON object")
            return cls(payload)
        except Exception as exc:
            logger.error("Failed to load correlation matrix from %s: %s", path, exc)
            return None


class CorrelationRiskGuard:
    """Guard that limits combined exposure to highly correlated symbols."""

    def __init__(
        self,
        correlation_matrix: CorrelationMatrix,
        max_correlated_exposure: Decimal,
        *,
        threshold: float = 0.75,
    ) -> None:
        if not (0.0 < threshold <= 1.0):
            raise ValueError("Correlation threshold must be within (0, 1].")
        if max_correlated_exposure <= 0:
            raise ValueError("max_correlated_exposure must be positive.")

        self.matrix = correlation_matrix
        self.max_correlated_exposure = max_correlated_exposure
        self.threshold = threshold

    async def validate_order(
        self,
        *,
        contract: SymbolContract,
        side: OrderSide,
        quantity: int,
        price: Decimal,
        portfolio: PortfolioState,
    ) -> None:
        """Validate that correlated exposure remains within limits."""
        if quantity <= 0:
            return
        if price <= 0:
            logger.debug(
                "Skipping correlation check for %s due to non-positive price %s",
                contract.symbol,
                price,
            )
            return

        symbol = contract.symbol.upper()
        correlated = self.matrix.get_correlated_symbols(symbol, self.threshold)
        if not correlated:
            return

        symbols_to_check = set(correlated)
        symbols_to_check.add(symbol)

        total_exposure = Decimal("0")
        # Compute projected exposure for the order symbol
        current_qty = await portfolio.position_quantity(symbol)
        projected_qty = current_qty + quantity if side == OrderSide.BUY else current_qty - quantity
        projected_symbol_exposure = abs(Decimal(projected_qty) * price)
        total_exposure += projected_symbol_exposure

        # Include current exposures of correlated symbols
        for other_symbol in symbols_to_check:
            if other_symbol == symbol:
                continue
            market_value = await portfolio.position_market_value(other_symbol)
            total_exposure += abs(market_value)

        if total_exposure > self.max_correlated_exposure:
            raise RuntimeError(
                "Correlated exposure limit exceeded: "
                f"{total_exposure} > {self.max_correlated_exposure} "
                f"for symbol {contract.symbol}"
            )
