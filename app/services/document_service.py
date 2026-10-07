"""
Read-only lookup helper for the Final Functional Requirements' document/
full-text API (spec items 2, 4, 5, 7).

Deliberately does NOT touch `app/services/fulltext/service.py` or
`app/services/fulltext/resolver.py` -- those own *acquiring* full text
(PDF -> XML -> HTML -> other, per the existing, unchanged chain) and are
out of scope here ("keep the existing full-text resolution approach ...
do not rebuild this unnecessarily"). This module only *reads* whatever
`DiscoveryDocument` row(s) that acquisition already produced, for the new
`GET /candidates/{id}/document` / `GET /documents/{id}` / candidate
workspace endpoints to serve.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import DiscoveryDocument


def get_representative_document(db: Session, candidate_id: uuid.UUID) -> DiscoveryDocument | None:
    """
    The single `DiscoveryDocument` row that best represents this
    candidate's current full-text acquisition state, or None if full-text
    acquisition has never even been attempted for it (no row at all --
    distinct from an attempt that explicitly found nothing, which is
    always one of the "unavailable"/"failed"/"unsupported" rows below).

    Preference order:
      1. The most recent row with `retrieval_status == "acquired"` -- the
         same row `app/services/fulltext/service.py::acquire_full_text`'s
         own short-circuit and `app/api/routers/rag.py::_build_rag_metadata`
         already treat as "the" document for a candidate.
      2. If none was ever acquired, the most recent row of ANY status, so
         an editor/Veda can still see *why* full text isn't available
         (`error_detail`) rather than getting a bare 404 when the engine
         genuinely tried and failed/found it unavailable.
      3. None, if `discovery_documents` has no row at all for this
         candidate -- i.e. full-text resolution was never even attempted.
    """
    acquired = db.execute(
        select(DiscoveryDocument)
        .where(DiscoveryDocument.candidate_id == candidate_id, DiscoveryDocument.retrieval_status == "acquired")
        .order_by(DiscoveryDocument.created_at.desc())
    ).scalars().first()
    if acquired is not None:
        return acquired

    return db.execute(
        select(DiscoveryDocument)
        .where(DiscoveryDocument.candidate_id == candidate_id)
        .order_by(DiscoveryDocument.created_at.desc())
    ).scalars().first()
