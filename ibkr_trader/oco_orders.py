"""Compatibility shim for ibkr_trader.oco_orders."""

from ibkr_trader.execution.oco_orders import OCOOrderManager, OCOPair

__all__ = ["OCOOrderManager", "OCOPair"]
