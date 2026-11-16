from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: SecretStr = SecretStr("")
    mysql_database: str = "commerce_insights"
    # Only automated tests should override this with SQLite.
    database_url: str | None = None
    clickhouse_host: str = "127.0.0.1"
    clickhouse_port: int = 8123
    clickhouse_user: str = "commerce"
    clickhouse_password: SecretStr = SecretStr("")
    clickhouse_database: str = "commerce_insights"
    cookie_secure: bool = False
    allowed_origins: list[str] = ["http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:8000"]
    session_hours: int = Field(12, ge=1, le=168)
    upload_limit_mb: int = Field(100, ge=1, le=1024)
    import_batch_size: int = Field(10000, ge=100, le=100000)
    query_timeout_seconds: int = Field(30, ge=1, le=300)
    instance_dir: Path = ROOT / "instance"
    recommender_url: str = ""

    @field_validator("mysql_database", "clickhouse_database")
    @classmethod
    def database_name(cls, value):
        import re

        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,62}", value):
            raise ValueError("数据库名只能包含字母、数字与下划线")
        return value

    def sql_url(self, with_database=True):
        if self.database_url:
            return self.database_url
        return URL.create(
            "mysql+pymysql",
            username=self.mysql_user,
            password=self.mysql_password.get_secret_value(),
            host=self.mysql_host,
            port=self.mysql_port,
            database=self.mysql_database if with_database else None,
            query={"charset": "utf8mb4"},
        )
