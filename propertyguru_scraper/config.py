import os
from dataclasses import dataclass
from sqlalchemy.engine import URL

@dataclass
class Config:
    # PostgreSQL Connection URL
    @property
    def DATABASE_URL(self) -> str:
        # Never use the root LangGraph DATABASE_URL (or its PG* settings).
        if os.getenv("PROPERTYGURU_DATABASE_URL"):
            return os.environ["PROPERTYGURU_DATABASE_URL"]
        return URL.create(
            "postgresql+psycopg2",
            username=os.getenv("PROPERTYGURU_PGUSER", "jerry"),
            password=os.getenv("PROPERTYGURU_PGPASSWORD") or None,
            host=os.getenv("PROPERTYGURU_PGHOST", "localhost"),
            port=int(os.getenv("PROPERTYGURU_PGPORT", "5432")),
            database=os.getenv("PROPERTYGURU_PGDATABASE", "propertyguru"),
        ).render_as_string(hide_password=False)

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
