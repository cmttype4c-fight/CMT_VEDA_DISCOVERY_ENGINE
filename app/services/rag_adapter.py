"""
RAG ingestion adapter (spec #25-26).

The existing CMT Veda RAG is NOT owned or rebuilt by this engine. This
module defines a clean `RAGIngestionAdapter` interface so the Discovery
Engine can hand off an approved candidate for ingestion without assuming
or inventing the real ingestion contract -- that contract "will be
verified separately" per spec #25.

- `MockRAGIngestionAdapter`: deterministic, in-memory, used by default
  (RAG_ADAPTER=mock) and in all tests. Lets the full pipeline be
  demonstrated end-to-end offline (spec #47).
- `HTTPRAGIngestionAdapter`: **PROVISIONAL, NOT PRODUCTION-READY.** A real
  HTTP implementation against a configurable endpoint
  (RAG_INGESTION_ENDPOINT), but two separate things are unconfirmed here,
  not just one:
    1. The actual CMT Veda RAG ingestion *contract* has never been
       confirmed by the RAG team -- the request/response shape below is a
       reasonable guess, not a verified integration.
    2. The *call site* (`app/api/routers/rag.py`'s `approve`/`retry`
       endpoints) invokes `submit()` synchronously, inline in an HTTP
       request, and awaits its result before responding. That is only
       safe for the mock adapter's instant, in-memory response. Plugging
       a real network call in here as-is -- without first moving the call
       onto the background worker -- would block API requests on an
       external service this engine doesn't control. See the module
       docstring in app/api/routers/rag.py for the full architectural
       note.
  Do not point this at a real RAG deployment until BOTH of the above are
  addressed. Not exercised in this sandbox (no network) -- see
  IMPLEMENTATION_STATUS.md. Logs a warning on instantiation as a reminder
  of point 1; point 2 is a call-site concern this class can't detect or
  warn about on its own.

Never touches FAISS/model files/embeddings/the RAG filesystem directly
(spec #25) -- only ever makes an ingestion *request* through this
interface.
"""
from __future__ import annotations

import abc
import uuid
from dataclasses import dataclass
from typing import Any

from app.config import get_settings
from app.logging_config import get_logger

logger = get_logger(component="rag_adapter")


@dataclass
class IngestionResult:
    success: bool
    external_ingestion_id: str | None = None
    error: str | None = None
    raw_response: dict[str, Any] | None = None


class RAGIngestionAdapter(abc.ABC):
    @abc.abstractmethod
    async def submit(self, metadata: dict[str, Any]) -> IngestionResult:
        """Submit one candidate's metadata for ingestion. Must not raise for
        ordinary failures -- return IngestionResult(success=False, error=...)
        so the caller can record `processing -> failed` (spec #51) and retry."""
        raise NotImplementedError

    @abc.abstractmethod
    async def get_status(self, external_ingestion_id: str) -> str:
        """Return one of: queued | processing | indexed | failed."""
        raise NotImplementedError

    @abc.abstractmethod
    async def remove(self, external_ingestion_id: str) -> bool:
        raise NotImplementedError


class MockRAGIngestionAdapter(RAGIngestionAdapter):
    """In-memory mock -- deterministic, offline-safe (spec #47)."""

    _store: dict[str, str] = {}

    async def submit(self, metadata: dict[str, Any]) -> IngestionResult:
        external_id = f"mock-ingest-{uuid.uuid4()}"
        self._store[external_id] = "indexed"  # mock adapter "indexes" instantly
        return IngestionResult(success=True, external_ingestion_id=external_id, raw_response={"mock": True, **metadata})

    async def get_status(self, external_ingestion_id: str) -> str:
        return self._store.get(external_ingestion_id, "failed")

    async def remove(self, external_ingestion_id: str) -> bool:
        return self._store.pop(external_ingestion_id, None) is not None


class HTTPRAGIngestionAdapter(RAGIngestionAdapter):
    """
    *** PROVISIONAL -- NOT A CONFIRMED INTEGRATION ***

    The exact request/response contract below is a best-guess placeholder,
    not something verified against the real CMT Veda RAG service (per the
    build instructions: "do NOT assume or invent the final ingestion
    endpoint of the existing RAG" -- spec #25). Treat this class as a
    skeleton to fill in once the RAG team confirms the actual endpoint
    shape, not as production-ready code. Adjust `submit`/`get_status`/
    `remove` bodies once that contract is verified -- structured this way
    so ONLY this class needs to change; nothing else in the engine
    depends on the wire format.
    """

    #: Explicit marker so callers/monitoring can detect "the provisional
    #: adapter is active" without string-matching the class name.
    PROVISIONAL = True

    def __init__(self):
        settings = get_settings()
        if not settings.rag_ingestion_endpoint:
            raise RuntimeError("RAG_INGESTION_ENDPOINT is required when RAG_ADAPTER=http")
        logger.warning(
            "provisional_rag_adapter_in_use",
            detail=(
                "HTTPRAGIngestionAdapter is a PROVISIONAL, unconfirmed integration -- "
                "the request/response contract has not been verified against the real "
                "CMT Veda RAG service. Do not treat successful calls as proof the "
                "integration is correct until the contract is confirmed."
            ),
            endpoint=settings.rag_ingestion_endpoint,
        )
        self.endpoint = settings.rag_ingestion_endpoint.rstrip("/")
        self.api_key = settings.rag_ingestion_api_key

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        return headers

    async def submit(self, metadata: dict[str, Any]) -> IngestionResult:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(f"{self.endpoint}/ingest", json=metadata, headers=self._headers())
                resp.raise_for_status()
                data = resp.json()
            return IngestionResult(success=True, external_ingestion_id=data.get("id"), raw_response=data)
        except Exception as exc:  # noqa: BLE001 - adapter boundary must not raise
            return IngestionResult(success=False, error=str(exc))

    async def get_status(self, external_ingestion_id: str) -> str:
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{self.endpoint}/ingest/{external_ingestion_id}", headers=self._headers())
            resp.raise_for_status()
            return resp.json().get("status", "failed")

    async def remove(self, external_ingestion_id: str) -> bool:
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.delete(f"{self.endpoint}/ingest/{external_ingestion_id}", headers=self._headers())
            return resp.status_code in (200, 202, 204)


def get_rag_adapter() -> RAGIngestionAdapter:
    settings = get_settings()
    if settings.rag_adapter == "http":
        return HTTPRAGIngestionAdapter()
    return MockRAGIngestionAdapter()
