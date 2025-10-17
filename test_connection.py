#!/usr/bin/env python3
"""Diagnostic script to test IBKR connection issues."""

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from ib_insync import IB, util

# Load environment variables
load_dotenv()

HOST = os.getenv("IBKR_HOST", "192.168.112.1")
PORT = int(os.getenv("IBKR_PORT", "7497"))
CLIENT_ID = int(os.getenv("CLIENT_ID", "92"))


async def test_direct_ib_connection():
    """Test direct ib_insync connection (like standalone script)."""
    print("\n" + "=" * 60)
    print("TEST 1: Direct ib_insync Connection")
    print("=" * 60)

    ib = IB()

    print(f"Attempting to connect to {HOST}:{PORT} with client_id={CLIENT_ID}")
    print(f"IB client state before connect: connected={ib.isConnected()}")

    try:
        # Direct connection like standalone script
        await ib.connectAsync(host=HOST, port=PORT, clientId=CLIENT_ID, timeout=10)

        print(f"✓ Connection successful!")
        print(f"  - Connected: {ib.isConnected()}")
        print(f"  - Client ID: {ib.client.clientId}")
        print(f"  - Accounts: {ib.managedAccounts()}")

        ib.disconnect()
        print("✓ Disconnected successfully")
        return True

    except asyncio.TimeoutError as e:
        print(f"✗ Connection TIMEOUT: {e}")
        return False
    except Exception as e:
        print(f"✗ Connection ERROR: {type(e).__name__}: {e}")
        return False


async def test_broker_class_connection():
    """Test connection through our IBKRBroker class."""
    print("\n" + "=" * 60)
    print("TEST 2: IBKRBroker Class Connection")
    print("=" * 60)

    # Import after sys.path setup
    from ibkr_trader.broker import IBKRBroker
    from ibkr_trader.config import IBKRConfig
    from ibkr_trader.safety import LiveTradingGuard

    print(f"Creating IBKRBroker with {HOST}:{PORT}, client_id={CLIENT_ID}")

    config = IBKRConfig(
        host=HOST,
        port=PORT,
        client_id=CLIENT_ID,
    )

    # Create safety guard (required by broker)
    guard = LiveTradingGuard(config, live_flag_enabled=False)

    broker = IBKRBroker(config, guard)

    print(f"Broker IB client state: connected={broker.ib.isConnected()}")

    try:
        await broker.connect(timeout=10.0)

        print(f"✓ Connection successful!")
        print(f"  - Connected: {broker.ib.isConnected()}")
        print(f"  - Client ID: {broker.ib.client.clientId}")
        print(f"  - Accounts: {broker.ib.managedAccounts()}")

        await broker.disconnect()
        print("✓ Disconnected successfully")
        return True

    except asyncio.TimeoutError as e:
        print(f"✗ Connection TIMEOUT: {e}")
        return False
    except Exception as e:
        print(f"✗ Connection ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_event_loop_diagnostics():
    """Check event loop state and configuration."""
    print("\n" + "=" * 60)
    print("TEST 3: Event Loop Diagnostics")
    print("=" * 60)

    loop = asyncio.get_running_loop()

    print(f"Event loop type: {type(loop).__name__}")
    print(f"Event loop running: {loop.is_running()}")
    print(f"Event loop closed: {loop.is_closed()}")

    # Check if util.patchAsyncio() was called
    print(f"\nib_insync util.patchAsyncio status:")
    print(f"  - asyncio module patched: {hasattr(asyncio, '_ib_insync_patched')}")

    # Try with patched asyncio
    util.patchAsyncio()
    print(f"  - Called util.patchAsyncio()")

    ib = IB()
    print(f"\nTrying connection with patched asyncio...")

    try:
        await ib.connectAsync(host=HOST, port=PORT, clientId=CLIENT_ID, timeout=10)
        print(f"✓ Connection successful with patched asyncio!")
        print(f"  - Connected: {ib.isConnected()}")
        ib.disconnect()
        return True
    except Exception as e:
        print(f"✗ Connection FAILED with patched asyncio: {type(e).__name__}: {e}")
        return False


async def test_with_different_client_id():
    """Test if client ID conflict is the issue."""
    print("\n" + "=" * 60)
    print("TEST 4: Different Client ID")
    print("=" * 60)

    # Try with a different client ID
    alt_client_id = CLIENT_ID + 1
    print(f"Trying with alternative client_id={alt_client_id}")

    ib = IB()

    try:
        await ib.connectAsync(host=HOST, port=PORT, clientId=alt_client_id, timeout=10)
        print(f"✓ Connection successful with client_id={alt_client_id}!")
        print(f"  - This suggests CLIENT_ID={CLIENT_ID} may be in use")
        ib.disconnect()
        return True
    except Exception as e:
        print(f"✗ Connection FAILED with client_id={alt_client_id}: {type(e).__name__}: {e}")
        return False


async def main():
    """Run all diagnostic tests."""
    print("\nIBKR Connection Diagnostics")
    print(f"Configuration: {HOST}:{PORT}, client_id={CLIENT_ID}")
    print(f"Python version: {sys.version}")

    try:
        import ib_insync
        print(f"ib_insync version: {ib_insync.__version__}")
    except:
        print("ib_insync version: unknown")

    results = {}

    # Test 1: Direct connection
    results['direct'] = await test_direct_ib_connection()
    await asyncio.sleep(1)  # Brief pause between tests

    # Test 2: Broker class connection
    results['broker'] = await test_broker_class_connection()
    await asyncio.sleep(1)

    # Test 3: Event loop diagnostics
    results['event_loop'] = await test_event_loop_diagnostics()
    await asyncio.sleep(1)

    # Test 4: Different client ID
    results['alt_client_id'] = await test_with_different_client_id()

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for test_name, success in results.items():
        status = "✓ PASS" if success else "✗ FAIL"
        print(f"{test_name:20s}: {status}")

    print("\n")


if __name__ == "__main__":
    asyncio.run(main())
