"""
Collector interface (spec #7-#9).

Collection priority, enforced by which collector class a source is
configured to use (never bypassed programmatically):

    Official API > RSS/Atom > Structured feed > Controlled webpage
    extraction > Manual submission

Every collector implementation must:
  - respect rate limits, ToS, and API quotas (see RateLimiter below)
  - never attempt to bypass paywalls, auth, or anti-bot mechanisms
  - emit `NormalizedRecord` objects only -- never partially-formed records
  - leave unavailable fields as None rather than guessing (spec #10)

Adding a new source type means adding a new BaseCollector subclass and
registering it in app/collectors/registry.py -- no database redesign
required (spec #5, #8).
"""
from __future__ import annotations

import abc
import asyncio
import hashlib
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, AsyncIterator, Optional


def normalize_text(text: Optional[str]) -> str:
    """
    Shared text-normalization used for BOTH matching (deduplication's
    fuzzy title fallback, app/services/deduplication.py) and
    change-detection (`NormalizedRecord.content_hash` below).

    This is the single source of truth for "are these two pieces of text
    the same content" -- keeping matching and hashing in sync here is
    what prevents a record the matcher considers identical from still
    hashing differently (which would misclassify a true DUPLICATE as
    UPDATED just because of a casing/punctuation difference upstream).
    """
    if not text:
        return ""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


@dataclass
class NormalizedRecord:
    """Mirrors app.models.source_record.DiscoverySourceRecord's normalized shape."""

    external_id: str
    canonical_url: Optional[str] = None
    title: Optional[str] = None
    authors: list[str] = field(default_factory=list)
    institution: Optional[str] = None
    journal: Optional[str] = None
    publisher: Optional[str] = None
    doi: Optional[str] = None
    pmid: Optional[str] = None
    clinical_trial_id: Optional[str] = None
    publication_date: Optional[date] = None
    abstract: Optional[str] = None
    description: Optional[str] = None
    raw_metadata: dict[str, Any] = field(default_factory=dict)

    def content_hash(self) -> str:
        """
        Deterministic hash of the fields that matter for change-detection
        (spec #12). Deliberately excludes fields like `first_seen_at` that
        change on every collection run even when nothing meaningful did.

        Title/abstract are run through `normalize_text` -- the same
        normalization the deduplication matcher uses -- so that a record
        matched as "the same" (e.g. via the fuzzy title fallback) is also
        guaranteed to hash the same when nothing substantive changed.
        Fields like `status` (inside raw_metadata), `doi`, and
        `publication_date` are compared as-is since a real change there
        (e.g. a trial's status) SHOULD flip the hash and produce UPDATED.
        """
        basis = "|".join(
            [
                normalize_text(self.title),
                normalize_text(self.abstract or self.description),
                (self.raw_metadata or {}).get("status", "") if isinstance(self.raw_metadata, dict) else "",
                self.doi or "",
                self.publication_date.isoformat() if self.publication_date else "",
            ]
        )
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()


class RateLimiter:
    """Simple async token-bucket-ish limiter: at most `rate_per_sec` calls/sec."""

    def __init__(self, rate_per_sec: float):
        self.min_interval = 1.0 / rate_per_sec if rate_per_sec > 0 else 0.0
        self._last_call = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        if self.min_interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            if elapsed < self.min_interval:
                await asyncio.sleep(self.min_interval - elapsed)
            self._last_call = time.monotonic()


class CollectorError(Exception):
    """Raised for collector-level failures; caught by the worker and recorded on the run."""


class BaseCollector(abc.ABC):
    """
    Abstract base for all collectors. Subclasses implement `collect()`.

    `source.configuration` (JSONB) carries collector-specific settings
    (vocabulary terms, feed URLs, historical-window months, etc.) so new
    sources of an already-supported type need zero code changes.
    """

    collection_method: str  # one of enums.CollectionMethod

    def __init__(self, source, http_client=None):
        self.source = source
        self.http_client = http_client

    @abc.abstractmethod
    async def collect(self, since: Optional[datetime] = None) -> AsyncIterator[NormalizedRecord]:
        """Yield NormalizedRecord objects. Must not raise on a single bad
        item -- log/skip it and continue; only raise CollectorError for
        failures affecting the whole run (e.g. auth failure, network down)."""
        raise NotImplementedError
        yield  # pragma: no cover - makes this an async generator for typing
