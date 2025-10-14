from model.data import FileCacheStore, MarketDataClient, YFinanceMarketDataSource
from model.training.industry_model import train_linear_industry_model

data_client = MarketDataClient(
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
    data_client=data_client,
)
print(f"Model saved at {artifact_path}")
