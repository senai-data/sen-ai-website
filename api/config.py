from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql://senai:senai-change-in-prod@postgres:5432/senai"

    # JWT
    jwt_secret: str = "CHANGE-THIS-SECRET-IN-PROD"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24  # 24 hours

    # Google OAuth
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "https://sen-ai.fr/api/auth/google/callback"

    # Stripe
    stripe_api_key: str = ""
    stripe_webhook_secret: str = ""

    # Frontend
    frontend_url: str = "https://sen-ai.fr"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
