from app.config import LOCAL_STOREFRONT_ORIGIN, Settings


def test_development_cors_includes_local_storefront() -> None:
    settings = Settings.model_construct(
        environment="development",
        cors_allowed_origins="http://localhost:5173",
    )

    assert settings.cors_origins == ["http://localhost:5173", LOCAL_STOREFRONT_ORIGIN]


def test_production_cors_uses_only_configured_origins() -> None:
    settings = Settings.model_construct(
        environment="production",
        cors_allowed_origins="https://admin.example.com",
    )

    assert settings.cors_origins == ["https://admin.example.com"]
