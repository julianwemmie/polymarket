from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./insider_trading.db"
    polymarket_clob_url: str = "https://clob.polymarket.com"
    polymarket_gamma_url: str = "https://gamma-api.polymarket.com"
    polygonscan_api_key: str = "demo"
    polygonscan_url: str = "https://api.etherscan.io/v2/api"

    model_config = {"env_file": ".env"}


settings = Settings()
