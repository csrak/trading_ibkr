# Model Training & Data Caching Guide

This walkthrough is designed to take you from a clean checkout to a trained industry model with cached market data and option chains. Each section builds on previous steps so you can follow along even if it's your first time using the tooling.

---

## 1. Prerequisites

1. **Install dependencies**
   ```bash
   uv sync
   ```
2. **Set up your environment variables**
   ```bash
   cp .env.example .env
   ```
   Update `.env` if you plan to use IBKR data (host, port, client IDs, etc.).

3. **Optional**: Sign in to TWS or IB Gateway if you want to pull data from IBKR right away. Paper trading mode is strongly recommended while experimenting.

---

## 2. Understand the Data Layers

We now have two parallel data stacks:

| Purpose          | Adapter                         | CLI Support                      | Cache Path (default)           |
|------------------|---------------------------------|----------------------------------|--------------------------------|
| Price Bars       | `YFinanceMarketDataSource`      | `train-model`                    | `data/cache/price_bars/...`    |
| Price Bars (IBKR)| `IBKRMarketDataSource`          | `train-model`                    | `data/cache/price_bars/...`    |
| Option Chains    | `YFinanceOptionChainSource`     | `cache-option-chain`             | `data/cache/option_chains/...` |
| Option Chains (IBKR) | `IBKROptionChainSource`     | `cache-option-chain`             | `data/cache/option_chains/...` |

All caches are plain CSV files so you can open them in a spreadsheet or notebook without special tooling.

---

## 3. Configure Training Defaults

`IBKRConfig` (loaded by `ibkr_trader.config.load_config`) now includes training-specific settings:

- `training_data_source`: default source for price bars (`"yfinance"` or `"ibkr"`).
- `training_cache_dir`: base path for cached datasets.
- `training_client_id`: dedicated IBKR client ID for historical data snapshots.
- `training_max_snapshots`: quota for requests per job (protects you from overusing IBKR snapshots).
- `training_snapshot_interval`: minimum seconds between IBKR historical calls.

Update these via environment variables (e.g., `IBKR_TRAINING_DATA_SOURCE=ibkr`) or by editing `.env`.

---

## 4. Prime Market Data (Optional but Recommended)

You can generate cached datasets once and reuse them for multiple training runs.

### 4.1 Price Bars

```bash
# YFinance (default)
ibkr-trader train-model \
  --target AAPL \
  --peer MSFT \
  --start 2023-01-01 \
  --end 2024-01-01 \
  --horizon 5
```

The command above:
1. Resolves data source (`training_data_source` unless overridden).
2. Downloads price bars (with caching).
3. Trains the industry regression and writes artifacts/visualizations.

To source data from IBKR instead:

```bash
ibkr-trader train-model \
  --target AAPL \
  --peer MSFT \
  --start 2023-01-01 \
  --end 2024-01-01 \
  --data-source ibkr \
  --max-snapshots 40 \
  --snapshot-interval 1.5 \
  --ibkr-client-id 205
```

**Tips**
- Stay within your snapshot quota; bump `--snapshot-interval` or lower `--max-snapshots` if you see pacing warnings.
- Artifacts land in `model/artifacts/industry_forecast/` (JSON model, predictions CSV, PNG charts).

### 4.2 Option Chains

```bash
# Cache an AAPL chain from yfinance
ibkr-trader cache-option-chain --symbol AAPL --expiry 2024-01-19

# Same request via IBKR with stricter throttling
ibkr-trader cache-option-chain \
  --symbol AAPL \
  --expiry 2024-01-19 \
  --data-source ibkr \
  --max-snapshots 10 \
  --snapshot-interval 2.0 \
  --ibkr-client-id 206
```

Outputs can be found under `data/cache/option_chains/<SYMBOL>/`.

---

## 5. Train via Script (Offline Example)

If you prefer Python scripts over CLI, the sample trainer now builds a cached client automatically:

```python
# trained_models/train_model_example.py
from model.data import FileCacheStore, MarketDataClient, YFinanceMarketDataSource
from model.training.industry_model import train_linear_industry_model

client = MarketDataClient(
    source=YFinanceMarketDataSource(),
    cache=FileCacheStore("data/cache"),
)

artifact_path = train_linear_industry_model(
    target_symbol="AAPL",
    peer_symbols=["MSFT", "GOOGL", "AMZN"],
    start="2023-01-01",
    end="2025-01-01",
    horizon_days=5,
    artifact_dir="model/artifacts/industry_forecast",
    data_client=client,
)
```

Run it with:

```bash
uv run python trained_models/train_model_example.py
```

---

## 6. Inspect Artifacts

After training, check:

- `model/artifacts/industry_forecast/AAPL_linear_model.json` — serialized coefficients and metadata.
- `model/artifacts/industry_forecast/AAPL_predictions.csv` — historical predictions aligned with training features.
- `AAPL_predicted_vs_actual.png` and `AAPL_peer_coefficients.png` — quick sanity checks.

For option chains, open the cached CSV files:

```bash
ls data/cache/option_chains/AAPL
```

---

## 7. Maintenance Tips

- **Purge caches**: remove old CSVs if they become stale (`rm -r data/cache/...`).
- **Rotate client IDs**: IBKR limits snapshots per client ID; using a dedicated training ID avoids conflicts with live trading sessions.
- **Track snapshot usage**: if you hit `SnapshotLimitError`, lower `--max-snapshots` or raise `--snapshot-interval`.
- **Document experiments**: write down which data source and cache settings produced a given artifact (include CLI flags in your logs).

---

## 8. Where to Go Next

- **Feature Engineering**: leverage cached option chains and price bars to experiment with IV-based features.
- **Backtesting**: use `ibkr-trader backtest` with the exported predictions to validate strategies.
- **Automation**: wrap CLI commands in scripts or cron jobs to refresh artifacts on a schedule.

Need more help? Open an issue or extend this guide with your own notes once you find a workflow that fits your style. Happy modeling!
