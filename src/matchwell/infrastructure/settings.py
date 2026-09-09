from collections.abc import Mapping
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from matchwell.domain.access import normalize_email

_SECRET_ENV_KEYS = (
    "DATABASE_URL",
    "MATCHWELL_ENVIRONMENT",
    "MATCHWELL_ADMIN_EMAILS",
    "MATCHWELL_AUTO_MIGRATE",
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "STRIPE_PILOT_PRICE_ID",
    "MATCHWELL_CHECKOUT_SUCCESS_URL",
    "MATCHWELL_CHECKOUT_CANCEL_URL",
    "MATCHWELL_BILLING_PORTAL_RETURN_URL",
    "MATCHWELL_BILLING_GRACE_DAYS",
)


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
    stripe_secret_key: SecretStr | None = Field(
        default=None,
        validation_alias="STRIPE_SECRET_KEY",
    )
    stripe_webhook_secret: SecretStr | None = Field(
        default=None,
        validation_alias="STRIPE_WEBHOOK_SECRET",
    )
    stripe_pilot_price_id: str | None = Field(
        default=None,
        validation_alias="STRIPE_PILOT_PRICE_ID",
    )
    checkout_success_url: str | None = Field(
        default=None,
        validation_alias="MATCHWELL_CHECKOUT_SUCCESS_URL",
    )
    checkout_cancel_url: str | None = Field(
        default=None,
        validation_alias="MATCHWELL_CHECKOUT_CANCEL_URL",
    )
    billing_portal_return_url: str | None = Field(
        default=None,
        validation_alias="MATCHWELL_BILLING_PORTAL_RETURN_URL",
    )
    billing_grace_period_days: int = Field(
        default=7,
        validation_alias="MATCHWELL_BILLING_GRACE_DAYS",
    )

    def reveal_database_url(self) -> str | None:
        if self.database_url is None:
            return None
        return self.database_url.get_secret_value()

    def reveal_stripe_secret_key(self) -> str | None:
        if self.stripe_secret_key is None:
            return None
        return self.stripe_secret_key.get_secret_value()

    def reveal_stripe_webhook_secret(self) -> str | None:
        if self.stripe_webhook_secret is None:
            return None
        return self.stripe_webhook_secret.get_secret_value()

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
        "MATCHWELL_BILLING_GRACE_DAYS": base.billing_grace_period_days,
    }
    database_url = base.reveal_database_url()
    if database_url is not None:
        values["DATABASE_URL"] = database_url
    stripe_secret_key = base.reveal_stripe_secret_key()
    if stripe_secret_key is not None:
        values["STRIPE_SECRET_KEY"] = stripe_secret_key
    stripe_webhook_secret = base.reveal_stripe_webhook_secret()
    if stripe_webhook_secret is not None:
        values["STRIPE_WEBHOOK_SECRET"] = stripe_webhook_secret
    if base.stripe_pilot_price_id is not None:
        values["STRIPE_PILOT_PRICE_ID"] = base.stripe_pilot_price_id
    if base.checkout_success_url is not None:
        values["MATCHWELL_CHECKOUT_SUCCESS_URL"] = base.checkout_success_url
    if base.checkout_cancel_url is not None:
        values["MATCHWELL_CHECKOUT_CANCEL_URL"] = base.checkout_cancel_url
    if base.billing_portal_return_url is not None:
        values["MATCHWELL_BILLING_PORTAL_RETURN_URL"] = base.billing_portal_return_url

    for key in _SECRET_ENV_KEYS:
        if key in secrets:
            values[key] = secrets[key]

    return Settings.model_validate(values)
