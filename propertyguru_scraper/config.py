import os
from dataclasses import dataclass

@dataclass
class Config:
    # PostgreSQL Connection URL
    DB_USER: str = os.getenv("PGUSER", "jerry")
    DB_PASSWORD: str = os.getenv("PGPASSWORD", "")
    DB_HOST: str = os.getenv("PGHOST", "localhost")
    DB_PORT: str = os.getenv("PGPORT", "5432")
    DB_NAME: str = os.getenv("PGDATABASE", "propertyguru")
    
    @property
    def DATABASE_URL(self) -> str:
        env_url = os.getenv("DATABASE_URL")
        if env_url:
            return env_url
        if self.DB_PASSWORD:
            return f"postgresql://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        return f"postgresql://{self.DB_USER}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    # Target site
    BASE_URL: str = "https://www.propertyguru.com.sg"
    SALE_PATH: str = "/property-for-sale"
    RENT_PATH: str = "/property-for-rent"

    # Scraping parameters
    IMPERSONATE: str = "chrome124"
    REQUEST_TIMEOUT: int = 25
    REQUEST_DELAY_MIN: float = 1.2
    REQUEST_DELAY_MAX: float = 2.5
    MAX_RETRIES: int = 4
    BACKOFF_FACTOR: float = 2.0
    BATCH_SIZE: int = 50

config = Config()
