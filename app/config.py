"""Application configuration using Pydantic Settings."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Application
    app_name: str = "llm-wiki-consumer"
    app_env: str = "development"
    debug: bool = True

    # PostgreSQL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "wiki"
    postgres_password: str = "wiki_secret"
    postgres_db: str = "llm_wiki"

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""

    # Qdrant
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_api_key: str = ""
    qdrant_collection: str = "wiki_chunks"

    # LLM
    llm_api_key: str = ""
    llm_model: str = "gpt-4"
    llm_base_url: str = "https://api.openai.com/v1"

    # WikiFs
    wikifs_cache_ttl: int = 3600  # 页面缓存 TTL（秒）

    # Storage
    storage_path: str = "./storage"  # 文件存储目录

    # Chunk
    chunk_size: int = 500  # chunk 切分大小（字符数）
    chunk_overlap: int = 50  # chunk 重叠行数

    @property
    def postgres_dsn(self) -> str:
        """PostgreSQL connection string."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        """Redis connection URL."""
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"


@lru_cache
def get_settings() -> Settings:
    """Get cached application settings."""
    return Settings()
