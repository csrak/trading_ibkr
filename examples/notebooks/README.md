# Analysis Notebooks

This directory will contain Jupyter notebooks for strategy analysis and evaluation.

## Planned Notebooks

### 1. `model_evaluation.ipynb`
- Load trained model artifacts
- Visualize predictions vs actuals
- Calculate evaluation metrics (RMSE, R², etc.)
- Plot peer coefficient weights
- Analyze forecast accuracy by time period

### 2. `backtest_analysis.ipynb`
- Load backtest results
- Plot P&L curves
- Calculate performance metrics (Sharpe, max drawdown, win rate)
- Analyze trade distribution
- Compare multiple strategy configurations

### 3. `strategy_optimization.ipynb`
- Grid search over strategy parameters
- Plot parameter sensitivity
- Find optimal parameter combinations
- Cross-validation across time periods

### 4. `market_microstructure.ipynb`
- Analyze order book dynamics
- Visualize bid-ask spread evolution
- Study order flow patterns
- Market impact analysis

## Usage

To use these notebooks:

1. Install Jupyter:
   ```bash
   uv pip install jupyter
   ```

2. Launch Jupyter:
   ```bash
   jupyter notebook examples/notebooks/
   ```

3. Open a notebook and run cells

## Data Requirements

Notebooks expect data in these locations:
- Model artifacts: `model/artifacts/*/`
- Backtest results: `results/backtests/*/`
- Historical data: `data/*.csv`
- Order book data: `data/order_book_*.csv`

## Coming Soon

Full notebook implementations are planned for Phase 2. For now, use the shell scripts in `examples/scripts/` for end-to-end workflows.
