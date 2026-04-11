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

    # Worker
    worker_id: str = "worker-1"
    poll_interval: int = 2

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
