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

    # --- Global scheduler (FINAL CORRECTION: SOURCE EXPANSION + 6:00 AM
    # IST SCHEDULER, TASK 4/5/6) --- one recurring trigger, not one cron
    # job per source; each source's own `frequency` still governs whether
    # it is actually due at any given cycle (app/worker/scheduler.py).
    # `scheduler_timezone` is never derived from the host/VPS system
    # timezone -- see app/worker/ist_scheduler.py's module docstring.
    scheduler_enabled: bool = True
    scheduler_hour: int = 6
    scheduler_minute: int = 0
    scheduler_timezone: str = "Asia/Kolkata"

    # --- PubMed ---
    pubmed_api_key: str = ""
    pubmed_base_url: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    pubmed_rate_limit_per_sec: float = 3

    # --- ClinicalTrials.gov ---
    clinicaltrials_base_url: str = "https://clinicaltrials.gov/api/v2"
    clinicaltrials_rate_limit_per_sec: float = 5

    # --- Europe PMC (CMT-specific overhaul, Phase 3/5) ---
    europepmc_base_url: str = "https://www.ebi.ac.uk/europepmc/webservices/rest"
    europepmc_rate_limit_per_sec: float = 3

    # --- ClinVar (FINAL FOCUSED CORRECTION, TASK 5) --- same NCBI
    # E-utilities family as PubMed above (db=clinvar instead of
    # db=pubmed); an NCBI API key raises the shared rate limit for both.
    # See app/collectors/clinvar.py's module docstring for the live
    # verification this pass performed against the real esummary shape.
    clinvar_base_url: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    clinvar_api_key: str = ""
    clinvar_rate_limit_per_sec: float = 3

    # --- Full-text/PDF acquisition (Phase 5/6) ---
    # "local" writes PDFs under this directory on disk with a
    # content-hash-derived filename (deduplicated); swappable for an
    # object-storage backend later without a schema change, since
    # discovery_documents.document_ref is just a reference string.
    fulltext_storage_backend: str = "local"
    fulltext_storage_dir: str = "/data/discovery-documents"
    fulltext_max_bytes: int = 50 * 1024 * 1024  # refuse to store anything larger than this

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
