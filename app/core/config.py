from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/rss_aggregator"
    rss_fetch_interval_seconds: int = 300
    rss_request_timeout_seconds: float = 20.0
    app_port: int = 8000
    environment: str = "development"
    internal_base_url: str = "http://app:8000"
    public_base_url: str = "http://localhost:8000"
    semantic_model_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    session_secret: str = "development-only-session-secret"
    demo_rss_feeds: str = (
        "Hacker News|https://hnrss.org/frontpage;"
        "BBC World|https://feeds.bbci.co.uk/news/world/rss.xml;"
        "NASA Breaking News|https://www.nasa.gov/rss/dyn/breaking_news.rss"
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    sse_enabled: bool = True

    def parsed_demo_feeds(self) -> list[tuple[str, str]]:
        feeds: list[tuple[str, str]] = []
        for entry in self.demo_rss_feeds.split(";"):
            if "|" not in entry:
                continue
            name, url = (part.strip() for part in entry.split("|", maxsplit=1))
            if name and url:
                feeds.append((name, url))
        return feeds


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
