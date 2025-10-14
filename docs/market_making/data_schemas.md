# Market Data Schemas

This document defines canonical schemas for market-making data ingestion and storage. All downstream components (quoter, order manager, simulator) should rely on these structures.

---

## 1. Equities Order Book Depth

### 1.1 Snapshot Record

Represents the full depth at a point in time.

| Field         | Type        | Description                                           |
|---------------|-------------|-------------------------------------------------------|
| `timestamp`   | ISO 8601    | UTC timestamp of the snapshot                         |
| `symbol`      | str         | Underlying symbol (e.g., `AAPL`)                      |
| `levels`      | list[Level] | Ordered list of depth levels (bid & ask)              |
| `venue`       | str         | Optional venue identifier (`SMART`, `ARCA`, etc.)     |

```
Level: {
  "side": "bid" | "ask",
  "price": float,
  "size": float,
  "level": int,             # 1 = best bid/ask, increments outward
  "num_orders": int | None  # optional
}
```

### 1.2 Incremental Updates (Optional)

To reduce storage, we can persist deltas:

| Field       | Type     | Description                         |
|-------------|----------|-------------------------------------|
| `timestamp` | ISO 8601 | Event time                          |
| `symbol`    | str      | Underlying symbol                   |
| `side`      | str      | `bid` or `ask`                      |
| `price`     | float    | Price level                         |
| `size`      | float    | Updated size (0 if level removed)   |
| `level`     | int      | Depth level index                   |

---

## 2. Trade Events

| Field       | Type     | Description                                         |
|-------------|----------|-----------------------------------------------------|
| `timestamp` | ISO 8601 | Execution time (UTC)                                |
| `symbol`    | str      | Underlying symbol                                   |
| `price`     | float    | Trade price                                         |
| `size`      | float    | Trade size                                          |
| `side`      | str      | Aggressor side (`buy` aggressive vs `sell`)         |
| `venue`     | str      | Optional venue identifier                           |

---

## 3. Option Surface Snapshots

### 3.1 Record

| Field        | Type     | Description                                |
|--------------|----------|--------------------------------------------|
| `timestamp`  | ISO 8601 | Snapshot time (UTC)                        |
| `symbol`     | str      | Underlying symbol                          |
| `expiry`     | str      | Expiry date (`YYYY-MM-DD`)                 |
| `strike`     | float    | Option strike                              |
| `right`      | str      | `C` or `P`                                 |
| `bid`        | float    | Bid price                                  |
| `ask`        | float    | Ask price                                  |
| `mid`        | float    | (Bid + Ask) / 2, computed                   |
| `last`       | float    | Last traded price, if available            |
| `implied_vol`| float    | Implied volatility (model or feed)         |
| `delta`      | float    | Optional greek                             |
| `gamma`      | float    | Optional greek                             |
| `vega`       | float    | Optional greek                             |
| `theta`      | float    | Optional greek                             |
| `underlying_price` | float | Underlier price at snapshot              |
| `source`     | str      | `yfinance`, `ibkr`, etc.                   |

### 3.2 Storage Recommendations

- Parquet for bulk storage (efficient filtering by expiry, strike).
- Optionally, a directory structure: `data/cache/options/<symbol>/<expiry>.parquet`.
- For quick lookups during training, we can maintain an index file (CSV/JSON) mapping expiries to paths.

---

## 4. Quote Instructions & Order States

### 4.1 Quote Instruction

| Field          | Type     | Description                                          |
|----------------|----------|------------------------------------------------------|
| `timestamp`    | ISO 8601 | When the quote was generated                         |
| `symbol`       | str      | Underlying symbol                                    |
| `side`         | str      | `bid` or `ask`                                       |
| `price`        | float    | Quoted price                                         |
| `size`         | float    | Quote size                                           |
| `quote_id`     | str      | Internal identifier                                  |
| `reason`       | str      | `inventory_skew`, `spread_update`, etc.              |
| `inventory`    | float    | Inventory state after the decision                   |
| `config_version`| str     | Reference to strategy configuration version          |

### 4.2 Order State Snapshot

| Field         | Type     | Description                                         |
|---------------|----------|-----------------------------------------------------|
| `order_id`    | str      | Broker order ID                                     |
| `quote_id`    | str      | Link to originating quote instruction               |
| `status`      | str      | `working`, `filled`, `cancelled`, `replaced`, etc.   |
| `submitted_at`| ISO 8601 | Submission timestamp                                |
| `updated_at`  | ISO 8601 | Last status change time                             |
| `filled_qty`  | float    | Total filled quantity                               |
| `avg_price`   | float    | Average execution price                             |
| `remaining_qty`| float   | Remaining quantity                                  |
| `venue`       | str      | Execution venue                                     |
| `remarks`     | str      | Optional freeform text                              |

---

## 5. Storage & Retrieval Patterns

1. **CSV/Parquet** for offline analysis and backtesting. Use directories with meaningful names:
   - `data/order_book/<symbol>/YYYYMMDD.parquet`
   - `data/trades/<symbol>/YYYYMMDD.parquet`
   - `data/options/<symbol>/<expiry>.parquet`

2. **Manifest files** (CSV or JSON) to track available datasets per symbol/expiry.

3. **In-memory ring buffers** for real-time modules (maintain the last N depth updates, trades, or quotes).

4. **Optional database**: if a user wants richer query capabilities, mirror the same schema in MongoDB or SQLite. Keep parity between file-based and database storage for portability.

---

## 6. Next Steps

1. Implement Python dataclasses / pydantic models corresponding to these schemas.
2. Extend the caching layer (`model/data`) to handle Level II and option surface storage.
3. Provide utilities for ingesting raw IBKR/yfinance data into these structures.
4. Define serialization/deserialization helpers (Parquet, CSV).
5. Integrate schema usage into the simulation harness and quoting engine.

These schemas will act as the contract between market data adapters, the order manager, and the backtesting/simulation environment. Adjustments should be versioned (e.g., via `config_version` fields) to maintain backward compatibility.
