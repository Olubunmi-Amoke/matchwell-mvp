from collections.abc import Mapping
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


def get_runtime_settings(secrets: Mapping[str, object]) -> Settings:
    base = get_settings()
    values: dict[str, object] = {
        "MATCHWELL_ENVIRONMENT": base.environment,
        "MATCHWELL_ADMIN_EMAILS": base.admin_emails,
        "MATCHWELL_AUTO_MIGRATE": base.auto_migrate,
    }
    database_url = base.reveal_database_url()
    if database_url is not None:
        values["DATABASE_URL"] = database_url

    for key in (
        "DATABASE_URL",
        "MATCHWELL_ENVIRONMENT",
        "MATCHWELL_ADMIN_EMAILS",
        "MATCHWELL_AUTO_MIGRATE",
    ):
        if key in secrets:
            values[key] = secrets[key]

    return Settings.model_validate(values)
