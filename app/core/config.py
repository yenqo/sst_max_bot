import os
from pydantic_settings import BaseSettings
from pydantic import ConfigDict

os.makedirs("./data", exist_ok=True)

class Settings(BaseSettings):
    bot_mode: str = "polling"
    max_bot_token: str = "demo_token"
    max_api_base_url: str = "https://platform-api2.max.ru"
    database_url: str = "sqlite+aiosqlite:///./data/vsbore.db"
    log_level: str = "INFO"
    timezone: str = "Europe/Moscow"

    model_config = ConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
