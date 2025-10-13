"""Simple connectivity check against a running TWS/Gateway instance.

Run this script manually to verify credentials outside the main application.
"""

from __future__ import annotations

from ibapi.client import EClient
from ibapi.wrapper import EWrapper


WIN_IP = "192.168.1.108"
PORT = 7497
CID = 1337  # unique client id for connectivity test


class TestApp(EWrapper, EClient):
    """Minimal EWrapper/EClient combo for a connectivity smoke test."""

    def __init__(self) -> None:
        EWrapper.__init__(self)
        EClient.__init__(self, wrapper=self)

    def nextValidId(self, order_id: int) -> None:
        print(f"Connected to IBKR, next valid order ID: {order_id}")
        self.disconnect()


def main() -> None:
    app = TestApp()
    app.connect(WIN_IP, PORT, clientId=CID)
    app.run()


if __name__ == "__main__":
    main()
