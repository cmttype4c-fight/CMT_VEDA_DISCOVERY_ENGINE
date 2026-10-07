# Discovery Engine -- Final API Contract (Veda-v1 integration)

This is the frozen contract Veda-v1 is built against, written for the
"CMT Veda -- Discovery Engine: Final Functional Requirements" task. See
`IMPLEMENTATION_STATUS.md`'s top section for what changed and why; this
document is the reference for *what the API looks like now*, not the
narrative of how it got there.

All endpoints are mounted under `API_PREFIX` (default `/api/v1`). Auth is
a bearer token mapped to a role (`discovery_administrator`, `reviewer`,
`service`) -- see `app/auth.py`. Every example below assumes
`Authorization: Bearer <token>`.

---

## 1. Candidate API

### `GET /candidates`

**Pagination (canonical: `limit`/`offset`).** Every response envelope is
`{items, total, limit, offset, has_more}`, always. Two equivalent input
styles:

| Style | Params | Example |
|---|---|---|
| Canonical | `limit`, `offset` | `?limit=20&offset=40` |
| Veda-style alias (accepted, converted internally) | `page`, `page_size` | `?page=3&page_size=20` -> `limit=20&offset=40` |

If `page` is present, it (and `page_size`, defaulting to `limit`'s
default of 50) take precedence over any `limit`/`offset` also sent.
Sending only `limit`/`offset` is completely unaffected by this addition.

**Status terminology (canonical: `"drafted"`).** The `newsletter_status`
filter accepts the canonical `NewsletterStatus` values (`not_selected`,
`selected`, `drafted`, `under_review`, `approved`, `scheduled`,
`published`, `rejected`, `archived`) plus the accepted alias `"draft"`,
normalized to `"drafted"` before filtering. **Every response always
returns `"drafted"`, never `"draft"`** -- the alias is input-only.

Other filters (`content_type`, `source_id`, `scope`, `gene`, `subtype`,
`topic`, `date_from`, `date_to`, `rag_status`, `q`) are unchanged.

### `GET /candidates/{id}`

Unchanged. Returns the full `CandidateOut` shape (identity, source
metadata, scientific classification, `newsletter_status`, `rag_status`,
`abstract`, `full_text_available`). Deliberately does **not** include
document/full-text content or a `document_id` -- see section 3 below for
why, and section 2 for where to get it.

---

## 2. Original document/full-text API

Full-text acquisition itself (PDF -> XML -> HTML -> other, Europe PMC)
is completely unchanged. These endpoints only *read* what it already
produced.

### `GET /candidates/{id}/document`

Returns the representative `discovery_documents` row for this candidate
(preferring one with `retrieval_status == "acquired"`; otherwise the
most recent attempt of any status, so you can still see why full text
isn't available). **404** means full-text resolution has never been
attempted at all for this candidate -- distinct from a 200 whose
`retrieval_status` is `unavailable`/`failed`/`unsupported` (resolution
was genuinely attempted; `error_detail` says why).

```json
{
  "id": "doc-uuid",
  "candidate_id": "candidate-uuid",
  "doi": "10.1234/x", "pmid": "42740795",
  "source": "europepmc", "full_text_source": "europepmc_oa_xml",
  "document_url": "https://europepmc.org/article/...",
  "retrieved_at": "2026-10-01T12:00:00Z",
  "mime_type": "application/xml", "full_text_format": "xml",
  "pdf_available": false, "file_size": 48213,
  "content_hash": "sha256-hex", "document_ref": "/data/discovery-documents/....xml",
  "license_provenance": "Europe PMC open-access full text (isOpenAccess=Y) ...",
  "retrieval_status": "acquired", "error_detail": null,
  "extracted_text": "... the full article body, verbatim ...",
  "extracted_char_count": 48213,
  "extraction_status": "success", "extraction_error": null,
  "created_at": "2026-10-01T12:00:05Z"
}
```

**`extracted_text` is populated ONLY when `retrieval_status == "acquired"`
and `extraction_status == "success"`.** It is `null` for every other
outcome -- Veda must never treat `GET /candidates/{id}`'s `abstract` as a
substitute for this field; the two are never interchangeable (spec: "abstract-only
material must not be represented as full text"). This is the field to
feed to Gemini for Newsletter drafting.

### `GET /documents/{document_id}`

Same shape, direct lookup by the `id` above (e.g. as surfaced in the
workspace's `document` block, section 3).

---

## 3. Candidate Workspace API

### `GET /candidates/{id}/workspace`

One consolidated read, to avoid reconstructing a candidate from many
calls:

```json
{
  "candidate": { "...": "full CandidateOut shape" },
  "source": { "id": "...", "source_name": "...", "source_type": "pubmed", "source_tier": "tier_1", "base_url": null, "collection_method": "official_api", "enabled": true },
  "analysis": { "cmt_relevance_score": 92, "...": "latest DiscoveryAnalysis, or null" },
  "document": {
    "id": "doc-uuid", "full_text_source": "europepmc_oa_xml", "document_url": "...",
    "full_text_format": "xml", "pdf_available": false, "retrieval_status": "acquired",
    "error_detail": null, "extraction_status": "success", "extracted_char_count": 48213,
    "license_provenance": "...", "retrieved_at": "..."
  },
  "editorial_draft": { "headline": "...", "is_ai_generated": true, "...": "current DiscoveryEditorialDraft, or null" },
  "newsletter": { "status": "selected", "section": "Research Digest", "published_at": null, "...": "or null" },
  "rag": { "status": "not_selected", "...": "Discovery's own rag_ingestion_requests row, or null" },
  "recent_audit": [ { "action": "discovered", "performed_by": "collector:PubMed", "performed_at": "..." } ]
}
```

Notes:

- **`document` here is a lightweight summary -- it never includes
  `extracted_text`.** Fetch that from `GET /candidates/{id}/document`
  (section 2) only when actually about to use it (e.g. feeding Gemini);
  this keeps the workspace response cheap regardless of article length.
- `newsletter`/`rag` are `null`, not an auto-created empty row, when the
  candidate has never been selected for that workflow -- viewing the
  workspace never creates one as a side effect.
- `rag` is Discovery's own existing tracking row (`rag_ingestion_requests`),
  already owned and already exposed via `GET /candidates/{id}/rag/status`.
  This is a reference to it, not a new RAG capability and not a change to
  the separate RAG implementation.

---

## 4. Newsletter API additions

Everything in this section is additive; every pre-existing Newsletter
endpoint (`select`/`review`/`approve`/`reject`/`schedule`/`DELETE
schedule`/`publish`/`archive`/`editorial-status`/`candidates/bulk`) is
unchanged.

### `PATCH /candidates/{id}/newsletter/section`

```json
// request
{ "section": "Research Digest" }
// response: the NewsletterItem, now carrying `section`
{ "status": "selected", "section": "Research Digest", "published_at": null, "...": "..." }
```

Pure metadata -- callable at **any** status, never a state-machine
transition. Role: reviewer-or-admin.

### `POST /candidates/{id}/newsletter/publish-now`

Explicit "Publish Now." Valid from `approved` (auto-schedules for `now()`
then immediately publishes) or `scheduled` (publishes immediately
instead of waiting). **422** from any other status. Role: admin. Zero
new `ALLOWED_TRANSITIONS` edges -- pure orchestration over the existing
`schedule()`/`publish()` functions.

### `GET /newsletter/published`

```
GET /newsletter/published?section=Research%20Digest&limit=20&offset=0
```

Newest-first by `published_at` (a new column, set once, the moment an
item is actually published -- never recomputed from `updated_at`, which
can change for unrelated reasons after publication). Response:

```json
{
  "items": [
    {
      "candidate_id": "...", "newsletter_item_id": "...",
      "section": "Research Digest", "published_at": "2026-10-07T09:00:00Z", "scheduled_for": null,
      "title": "...", "authors": ["..."], "journal": "...", "source_name": "PubMed",
      "source_url": "https://pubmed.ncbi.nlm.nih.gov/...", "doi": "...", "pmid": "...",
      "content_type": "research_paper", "full_text_available": true,
      "headline": "...", "summary": "...", "why_it_matters": "...", "key_points": ["..."],
      "is_ai_generated": true
    }
  ],
  "total": 1, "limit": 20, "offset": 0, "has_more": false
}
```

Built as exactly 2 database queries regardless of page size (one joined
candidate+item query, one bulk current-draft lookup for the page) -- no
N+1 candidate-detail loop. `headline`/`summary`/`why_it_matters`/
`key_points` are the candidate's current editorial draft (AI/editorial
content, `is_ai_generated` says so); everything else is the original
scientific source (candidate fields).

---

## 5. Scheduler / worker

Unchanged. The 06:00 Asia/Kolkata daily source-collection scheduler
(`app/worker/ist_scheduler.py`, `app/worker/scheduler.py`) is untouched
and remains entirely separate from Newsletter publication (section 4) and
RAG processing.

## 6. Auth / configuration requirements

No new environment variables or auth roles. Every new endpoint reuses
the existing `discovery_administrator`/`reviewer`/`service` roles and the
existing `AUTH_ADMIN_TOKENS`/`AUTH_REVIEWER_TOKENS`/`AUTH_SERVICE_TOKENS`
env vars (see `app/auth.py`, `.env.example`).

## 7. Database migration

`alembic upgrade head` applies `0004_final_api_contract.py`: two
nullable, additive columns on `newsletter_items` (`section`,
`published_at`) plus an index on `published_at`. Safe to run ahead of
deploying the new application code.

---

## Smoke test (run after deploying, against a real candidate id)

```bash
export TOKEN="<a reviewer-or-admin token>"
export BASE="https://<your-host>/api/v1"
export CID="<a real candidate id>"

# 1. Candidate contract: page/page_size alias + draft/drafted alias
curl -s "$BASE/candidates?page=1&page_size=5" -H "Authorization: Bearer $TOKEN" | head -c 400
curl -s "$BASE/candidates?newsletter_status=draft" -H "Authorization: Bearer $TOKEN" | head -c 400

# 2. Document/full-text API
curl -s "$BASE/candidates/$CID/document" -H "Authorization: Bearer $TOKEN"

# 3. Candidate workspace
curl -s "$BASE/candidates/$CID/workspace" -H "Authorization: Bearer $TOKEN"

# 4. Newsletter section (reviewer-or-admin token)
curl -s -X PATCH "$BASE/candidates/$CID/newsletter/section" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"section": "Research Digest"}'

# 5. Published feed
curl -s "$BASE/newsletter/published?limit=5" -H "Authorization: Bearer $TOKEN"

# 6. Publish-now (admin token; candidate must already be approved/scheduled)
curl -s -X POST "$BASE/candidates/$CID/newsletter/publish-now" -H "Authorization: Bearer $ADMIN_TOKEN"
```

A 401/403 means the token's role doesn't match the endpoint's requirement
(see section 6); a 404 on `/document` for a candidate that should have
full text means resolution was never attempted -- see
`IMPLEMENTATION_STATUS.md`'s full-text diagnosis section from the
previous pass.

---

## Requirements for Veda-v1 / Lovable (not implemented here -- Discovery Engine only)

1. **Pagination**: switch `GET /candidates` calls to `limit`/`offset` when
   convenient, or keep sending `page`/`page_size` -- both now work. Don't
   rely on any other endpoint accepting `page`/`page_size` yet; only
   `GET /candidates` does.
2. **Status terminology**: display and filter against `"drafted"`, never
   `"draft"`, in any UI logic that reads `newsletter_status` directly from
   a candidate response (the `GET /candidates` query alias is input-only
   and does not change what's returned).
3. **Full text**: fetch it from `GET /candidates/{id}/document`'s
   `extracted_text`, only when non-null. Never pass `abstract` to Gemini
   labeled as full text.
4. **Candidate workspace**: prefer `GET /candidates/{id}/workspace` over
   separate calls to candidates/analysis/editorial-draft/newsletter/rag
   for a single candidate's detail view.
5. **Published Newsletter page**: build it from `GET
   /newsletter/published`, not a per-candidate fetch loop.
6. **Newsletter section/destination**: persist it server-side via `PATCH
   /candidates/{id}/newsletter/section` rather than only in browser
   state, if the Veda-v1 UI currently keeps it locally.
7. Everything else (Gemini Newsletter generation, RAG ingestion UI, Ask
   Veda) stays exactly where it already is -- no change required on
   Veda-v1's side for those.
