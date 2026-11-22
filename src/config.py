from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/neomarket",
        description="PostgreSQL async connection URL",
    )
    jwt_secret: str = Field(
        default="dev-secret-change-in-production",
        description="Secret key for JWT signing",
    )
    jwt_algorithm: str = Field(default="HS256", description="JWT algorithm")
    jwt_expires_minutes: int = Field(default=30, description="JWT token expiry in minutes")
    app_host: str = Field(default="0.0.0.0", description="Application host")
    app_port: int = Field(default=8000, description="Application port")
    debug: bool = Field(default=False, description="Debug mode")

    # External service configuration
    moderation_service_url: str = Field(
        default="http://moderation:8000",
        description="Moderation service URL",
    )
    b2b_to_mod_key: str = Field(
        default="dev-b2b-to-mod-key",
        description="Service key for B2B to Moderation communication",
    )
    mod_to_b2b_key: str = Field(
        default="dev-mod-to-b2b-key",
        description="Service key for Moderation to B2B communication",
    )
    b2c_service_url: str = Field(
        default="http://b2c:8000",
        description="B2C service URL",
    )
    b2b_to_b2c_key: str = Field(
        default="dev-b2b-to-b2c-key",
        description="Service key for B2B to B2C communication",
    )
    catalog_b2b_unavailable: bool = Field(
        default=False,
        description="Simulate B2B being unavailable for the catalog (used in tests/ops)",
    )


settings = Settings()
