from datetime import time
from functools import lru_cache

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_STOREFRONT_ORIGIN = "http://localhost:3000"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Bagh-e-Khas API"
    environment: str = "development"
    database_url: str = Field(validation_alias=AliasChoices("DATABASE_URL", "NEON_DB_CONNECTION"))
    jwt_secret: str = Field(min_length=32, validation_alias="JWT_SECRET")
    jwt_access_token_minutes: int = Field(
        default=30,
        ge=5,
        le=1440,
        validation_alias="JWT_ACCESS_TOKEN_MINUTES",
    )
    jwt_refresh_token_days: int = Field(
        default=7,
        ge=1,
        le=90,
        validation_alias="JWT_REFRESH_TOKEN_DAYS",
    )
    rider_jwt_access_token_minutes: int = Field(
        default=480,
        ge=5,
        le=720,
        validation_alias="RIDER_JWT_ACCESS_TOKEN_MINUTES",
    )
    rider_refresh_token_days: int = Field(
        default=7,
        ge=1,
        le=90,
        validation_alias="RIDER_REFRESH_TOKEN_DAYS",
    )
    rider_route_workflow_enabled: bool = Field(
        default=False,
        validation_alias="RIDER_ROUTE_WORKFLOW_ENABLED",
    )
    google_cloud_project_id: str | None = Field(
        default=None,
        validation_alias="GOOGLE_CLOUD_PROJECT_ID",
    )
    google_route_optimization_credentials_base64: SecretStr | None = Field(
        default=None,
        validation_alias="GOOGLE_ROUTE_OPTIMIZATION_CREDENTIALS_BASE64",
    )
    route_optimization_timeout_seconds: int = Field(
        default=10,
        ge=2,
        le=60,
        validation_alias="ROUTE_OPTIMIZATION_TIMEOUT_SECONDS",
    )
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "bagh-e-khas-api"
    jwt_audience: str = "bagh-e-khas-admin"
    rider_jwt_audience: str = "bagh-e-khas-rider"
    cors_allowed_origins: str = Field(default="", validation_alias="CORS_ALLOWED_ORIGINS")
    delivery_cutoff_hour: int = Field(
        default=15, ge=0, le=23, validation_alias="DELIVERY_CUTOFF_HOUR"
    )
    delivery_default_time: time = Field(
        default=time(18, 0), validation_alias="DELIVERY_DEFAULT_TIME"
    )
    aws_access_key_id: SecretStr
    aws_secret_access_key: SecretStr
    aws_storage_bucket_name: str
    aws_s3_url: str
    s3direct_region: str
    s3_presigned_url_expiration_seconds: int = Field(default=300, ge=60, le=900)
    max_request_body_bytes: int = Field(
        default=1_048_576,
        ge=1024,
        le=10_485_760,
        validation_alias="MAX_REQUEST_BODY_BYTES",
    )
    login_requests_per_minute: int = Field(
        default=10,
        ge=1,
        le=1000,
        validation_alias="LOGIN_REQUESTS_PER_MINUTE",
    )
    public_order_requests_per_minute: int = Field(
        default=30,
        ge=1,
        le=1000,
        validation_alias="PUBLIC_ORDER_REQUESTS_PER_MINUTE",
    )

    @field_validator("database_url")
    @classmethod
    def database_url_must_be_postgres(cls, value: str) -> str:
        if not value.startswith(("postgres://", "postgresql://", "postgresql+psycopg://")):
            raise ValueError("DATABASE_URL must be a PostgreSQL connection string")
        return value

    @field_validator("aws_storage_bucket_name", "s3direct_region")
    @classmethod
    def s3_setting_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("S3 configuration values cannot be blank")
        return stripped

    @field_validator("aws_s3_url")
    @classmethod
    def s3_url_must_be_http(cls, value: str) -> str:
        stripped = value.strip().rstrip("/")
        if not stripped.startswith(("https://", "http://")):
            raise ValueError("AWS_S3_URL must be an HTTP(S) public base URL")
        return stripped

    @property
    def async_database_url(self) -> str:
        if self.database_url.startswith("postgresql+psycopg://"):
            return self.database_url
        if self.database_url.startswith("postgres://"):
            return self.database_url.replace("postgres://", "postgresql+psycopg://", 1)
        return self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)

    @property
    def cors_origins(self) -> list[str]:
        configured_origins = [
            origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()
        ]
        if self.environment.casefold() != "development":
            return configured_origins
        return list(dict.fromkeys([*configured_origins, LOCAL_STOREFRONT_ORIGIN]))

    @property
    def delivery_cutoff(self) -> time:
        return time(self.delivery_cutoff_hour)


@lru_cache
def get_settings() -> Settings:
    return Settings()
