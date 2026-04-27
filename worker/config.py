from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://senai:senai-change-in-prod@postgres:5432/senai"

    # HaloScan
    haloscan_api_key: str = ""
    haloscan_base_url: str = "https://api.haloscan.com"

    # LLM (platform defaults — clients can override with their own keys)
    gemini_api_key: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # OAuth (worker needs to decrypt tokens for sync jobs)
    oauth_fernet_key: str = ""

    # Google OAuth (for token refresh in worker)
    google_client_id: str = ""
    google_client_secret: str = ""

    # Google Ads API
    google_ads_developer_token: str = ""
    google_ads_api_version: str = "v23"

    # Worker
    worker_id: str = "worker-1"
    poll_interval: int = 2

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
