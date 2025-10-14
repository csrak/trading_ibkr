from model.training.industry_model import train_linear_industry_model

artifact_path = train_linear_industry_model(
    target_symbol="AAPL",
    peer_symbols=["MSFT", "GOOGL", "AMZN"],
    start="2023-01-01",
    end="2025-01-01",
    horizon_days=5,
    artifact_dir="model/artifacts/industry_forecast",
)
print(f"Model saved at {artifact_path}")
