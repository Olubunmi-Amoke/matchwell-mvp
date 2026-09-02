from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from matchwell.domain.access import normalize_email


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["development", "test", "staging", "production"] = Field(
        default="development",
        validation_alias="MATCHWELL_ENVIRONMENT",
    )
    database_url: SecretStr | None = Field(
        default=None,
        validation_alias="DATABASE_URL",
    )
    admin_emails: str = Field(
        default="",
        validation_alias="MATCHWELL_ADMIN_EMAILS",
    )
    auto_migrate: bool = Field(
        default=False,
        validation_alias="MATCHWELL_AUTO_MIGRATE",
    )

    def reveal_database_url(self) -> str | None:
        if self.database_url is None:
            return None
        return self.database_url.get_secret_value()

    def normalized_admin_emails(self) -> frozenset[str]:
        return frozenset(
            normalize_email(email)
            for email in self.admin_emails.split(",")
            if email.strip()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
