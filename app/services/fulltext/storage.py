"""
Document storage abstraction (CMT-specific overhaul, Phase 6).

Stores only the acquired file itself; all queryable metadata lives in
`discovery_documents` (app/models/document.py) -- per explicit instruction
not to store large PDF binaries directly in PostgreSQL.

`LocalDiskDocumentStorage` is the only backend implemented in this pass
(FULLTEXT_STORAGE_BACKEND=local, the default) -- it writes under
`settings.fulltext_storage_dir`, keyed by the file's own SHA-256 hash, so
storing the same physical document twice (e.g. resolved independently for
two candidates, such as a preprint and its published version) is a no-op
write and a shared reference, which is what gives
discovery_documents.content_hash's uniqueness constraint its actual
deduplication effect. An object-storage backend (S3-compatible, etc.) can
be added later as a second class implementing the same `save()`/`exists()`
interface, without any schema change, since `document_ref` is just an
opaque reference string as far as the rest of the engine is concerned --
deliberately not assumed or built in this pass (per instruction: "do not
invent an external storage provider without checking the current
deployment architecture," which this sandbox cannot do -- no VPS access).
"""
from __future__ import annotations

import abc
import hashlib
import os
from dataclasses import dataclass

from app.config import get_settings


@dataclass
class StoredDocument:
    document_ref: str
    content_hash: str
    file_size: int


class DocumentStorage(abc.ABC):
    @abc.abstractmethod
    def save(self, content: bytes, *, suggested_ext: str = ".pdf") -> StoredDocument:
        raise NotImplementedError

    @abc.abstractmethod
    def exists(self, content_hash: str) -> bool:
        raise NotImplementedError


class LocalDiskDocumentStorage(DocumentStorage):
    def __init__(self, base_dir: str | None = None):
        settings = get_settings()
        self.base_dir = base_dir or settings.fulltext_storage_dir

    def _path_for_hash(self, content_hash: str, ext: str) -> str:
        # Two-level fan-out (like git objects) so one directory never
        # accumulates an unbounded number of files.
        return os.path.join(self.base_dir, content_hash[:2], f"{content_hash}{ext}")

    def exists(self, content_hash: str) -> bool:
        for ext in (".pdf", ".xml", ".html", ""):
            if os.path.exists(self._path_for_hash(content_hash, ext)):
                return True
        return False

    def save(self, content: bytes, *, suggested_ext: str = ".pdf") -> StoredDocument:
        content_hash = hashlib.sha256(content).hexdigest()
        path = self._path_for_hash(content_hash, suggested_ext)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.exists(path):
            # Write to a temp file first, then atomically rename -- avoids
            # a half-written file being treated as valid if the process
            # dies mid-write (this is meant to be idempotent/retryable
            # per the full-text acquisition requirement).
            tmp_path = f"{path}.tmp-{os.getpid()}"
            with open(tmp_path, "wb") as fh:
                fh.write(content)
            os.replace(tmp_path, path)
        return StoredDocument(document_ref=path, content_hash=content_hash, file_size=len(content))


def get_document_storage() -> DocumentStorage:
    settings = get_settings()
    if settings.fulltext_storage_backend == "local":
        return LocalDiskDocumentStorage()
    raise NotImplementedError(
        f"fulltext_storage_backend={settings.fulltext_storage_backend!r} is not implemented -- "
        "only 'local' exists in this pass. See module docstring."
    )
