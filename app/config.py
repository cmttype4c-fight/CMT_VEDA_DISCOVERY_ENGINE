"""
Central application configuration.

Everything that varies between environments (dev/staging/prod) or that is a
secret comes from environment variables. Never hard-code credentials here.
See .env.example for the full list of supported variables.
"""
from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- App ---
    app_env: str = "development"
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"

    # --- Database ---
    database_url: str = "postgresql+psycopg://discovery:discovery@localhost:5432/discovery_engine"

    # --- Auth placeholders ---
    auth_admin_tokens: str = "changeme-admin-token"
    auth_reviewer_tokens: str = "changeme-reviewer-token"
    auth_service_tokens: str = "changeme-service-token"

    # --- Worker ---
    worker_concurrency: int = 3
    worker_poll_interval_seconds: int = 5
    job_max_attempts: int = 5
    job_backoff_base_seconds: int = 30

    # --- PubMed ---
    pubmed_api_key: str = ""
    pubmed_base_url: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    pubmed_rate_limit_per_sec: float = 3

    # --- ClinicalTrials.gov ---
    clinicaltrials_base_url: str = "https://clinicaltrials.gov/api/v2"
    clinicaltrials_rate_limit_per_sec: float = 5

    # --- AI provider ---
    ai_provider: str = "mock"  # "mock" | "anthropic"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    # --- RAG adapter ---
    rag_adapter: str = "mock"  # "mock" | "http"
    rag_ingestion_endpoint: str = ""
    rag_ingestion_api_key: str = ""

    @property
    def admin_tokens(self) -> List[str]:
        return [t.strip() for t in self.auth_admin_tokens.split(",") if t.strip()]

    @property
    def reviewer_tokens(self) -> List[str]:
        return [t.strip() for t in self.auth_reviewer_tokens.split(",") if t.strip()]

    @property
    def service_tokens(self) -> List[str]:
        return [t.strip() for t in self.auth_service_tokens.split(",") if t.strip()]


@lru_cache
def get_settings() -> "Settings":
    return Settings()
