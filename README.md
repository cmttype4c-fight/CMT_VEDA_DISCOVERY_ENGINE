# CMT Veda AI — Discovery Engine

Backend-only Discovery Engine for CMT Veda AI: source collection,
normalization, deduplication, Veda Intelligence classification, editorial
generation, and the human-controlled newsletter and RAG workflows. The
frontend (admin dashboard, review screens, newsletter UI, RAG queue UI)
is built separately in Lovable and consumes this engine purely through
its HTTP API.

**Before anything else, read `IMPLEMENTATION_STATUS.md`.** This engine
was built in a sandboxed environment with no network access and no
Python package registry access, so while every component below was
written and syntax-checked, it has **not** been executed end-to-end by
the author. The status table tells you exactly what to verify first.

---

## Architecture

```
EXTERNAL SOURCES (PubMed, ClinicalTrials.gov, RSS feeds, manual submission)
        |
        v
   COLLECTION  (app/collectors/*)
        |
        v
   NORMALIZE   (app/services/normalization.py)
        |
        v
  DEDUPLICATE  (app/services/deduplication.py)
        |
        v
   CANDIDATE   (app/services/candidate_service.py, app/models/candidate.py)
        |
        v
 VEDA INTELLIGENCE (app/services/intelligence/*)
        |
   +----+----+
   v         v
NEWSLETTER   RAG PATH
  PATH    (app/services/workflows/rag_workflow.py,
(app/services/    app/services/rag_adapter.py)
 workflows/
 newsletter_workflow.py)
```

Everything runs as three Docker services (`docker-compose.yml`): a
FastAPI API process, a background worker process, and PostgreSQL. No
Redis — the worker uses PostgreSQL-backed job coordination
(`SELECT ... FOR UPDATE SKIP LOCKED`).

## Project layout

```
app/
  models/          SQLAlchemy ORM models (one file per table/group)
  schemas/         Pydantic request/response schemas
  collectors/       PubMed, ClinicalTrials.gov, generic RSS, registry
  services/
    normalization.py, deduplication.py, candidate_service.py,
    audit_service.py, taxonomy_service.py, rag_adapter.py
    intelligence/   AI provider abstraction, rules stage, orchestrator,
                     editorial generation
    workflows/      Newsletter and RAG state machines
  worker/           Job queue, handlers, main loop, scheduler
  api/
    routers/        One router per resource (sources, runs, candidates,
                     analysis, editorial, newsletter, rag, manual, audit,
                     health, document, workspace)
  main.py           FastAPI app assembly
  auth.py           Role-based auth hooks (placeholder until real
                     CMT Veda auth is integrated)
  config.py         Environment-variable-driven settings
alembic/            Database migrations (targets PostgreSQL)
tests/              pytest suite + golden dataset + fixture responses
Dockerfile, docker-compose.yml, .env.example
```

## Database

11 required tables plus one supporting table:

| Table | Purpose |
|---|---|
| `discovery_sources` | Configurable source registry |
| `discovery_runs` | One row per collection execution |
| `discovery_source_records` | Normalized collector output, with history (`is_current`, `supersedes_record_id`) |
| `discovery_candidates` | The central business object (3 logical layers: identity, source & evidence, scientific classification) |
| `discovery_analysis` | Versioned Veda Intelligence output |
| `discovery_editorial_drafts` | Versioned editorial drafts |
| `newsletter_items` / `newsletter_publications` | Newsletter workflow state + actual issues |
| `rag_ingestion_requests` | RAG workflow state, verification checklist, adapter results |
| `discovery_taxonomy` | Admin-extendable vocabulary (genes, subtypes, topics, disease synonyms) |
| `discovery_audit_log` | Append-only audit trail |
| `discovery_jobs` *(supporting)* | PostgreSQL-backed worker queue |

Models use portable column types (`app/models/base.py`: `GUID`,
`PortableJSON`) so the exact same SQLAlchemy models can run against
SQLite for fast local tests and PostgreSQL in production — but the
**Alembic migration targets PostgreSQL specifically** (native `UUID` and
`JSONB`), since that's the only database this engine is meant to run
against in staging/production.

A schema note on `RagStatus`: the spec's state list (#23) enumerates 8
states, but spec #51 explicitly contrasts a technical `failed` outcome
with a human/content `rejected` outcome — which requires `rejected` to
be a real, distinct status. I added it as a 9th `RagStatus` value rather
than overloading `failed` or `not_selected` for rejections; see
`app/models/enums.py` for the inline note.

## Collection engine

- **PubMed/NCBI** (`app/collectors/pubmed.py`): esearch → efetch against
  the real E-utilities API. Vocabulary is configurable per-source
  (`source.configuration.vocabulary`), not hard-coded.
- **ClinicalTrials.gov** (`app/collectors/clinicaltrials.py`): API v2
  `/studies` endpoint. Trial status changes flow into the content hash
  so a `Recruiting → Active` transition is correctly classified as
  `UPDATED`, not `DUPLICATE`, preserving history.
- **Generic RSS/Atom** (`app/collectors/generic_rss.py`): reusable for
  CMTA/HNF/CMT Research Foundation-style feeds via
  `source.configuration.feed_url` — no new code needed per organisation.
- **Registry** (`app/collectors/registry.py`): maps a source to its
  collector class. Sources without a production-live collector yet
  (NIH/NINDS, RDCRN, controlled webpage extraction) get a
  `NotImplementedCollector` that fails loudly rather than silently
  collecting nothing.

Every collector's parsing logic is a pure, network-free function
(`parse_pubmed_xml`, `parse_clinicaltrials_study`, `parse_feed`) so it's
unit-testable against recorded-shape fixture data.

## Deduplication

Deterministic match order: **DOI → PMID → ClinicalTrials.gov ID →
canonical URL → normalized title + author/institution/date**. Outcomes
are `NEW`, `DUPLICATE`, or `UPDATED`. An `UPDATED` record supersedes the
previous one (`is_current = false`, `supersedes_record_id` set) rather
than overwriting it, so source history is preserved.

## Veda Intelligence

Pipeline: **Normalized Candidate → Deterministic Rules → AI Analysis →
Structured Result** (`app/services/intelligence/veda_intelligence.py`).

AI-safety mechanisms actually implemented, not just described:
- The AI is only ever asked to *propose* classification/scoring signals
  (scope, genes, topics, scores) — never facts like DOI/PMID/authors.
- **Concrete gene-hallucination guard**: any AI-proposed gene symbol that
  doesn't appear in the deterministic rules-stage matches *and* doesn't
  literally appear in the candidate's own title/abstract text is dropped
  before it reaches the candidate record. Dropped genes are recorded in
  `ai_output.dropped_unverified_genes` for transparency.
- **Prompt-injection wrapping**: all source-derived text is wrapped in an
  explicit `<untrusted_source_content>` marker with an instruction to
  treat it as data, never commands.
- If the AI call fails or returns unparsable output, analysis still
  completes from the rules stage alone, with confidence capped low
  rather than blocking the pipeline.
- Every analysis is versioned (`analysis_version`, `is_latest`).

**AI provider abstraction** (`app/services/intelligence/ai_provider.py`):
a `MockAIProvider` (deterministic, offline, default) and an
`AnthropicAIProvider` (real HTTP calls to `/v1/messages`) both implement
the same `AIProvider` interface. Switching providers is one env var
(`AI_PROVIDER=anthropic` + `ANTHROPIC_API_KEY`) — nothing else changes.

## Editorial generation

Source-grounded, versioned, non-diagnostic drafts
(`app/services/intelligence/editorial_service.py`). Editing a draft
**never** touches the original source record or a prior draft version —
it inserts a new `discovery_editorial_drafts` row and marks the previous
one not-current. AI-generated vs. human-edited content is distinguishable
via `is_ai_generated` / `last_edited_by`.

## Workflows

Both newsletter (`app/services/workflows/newsletter_workflow.py`) and RAG
(`app/services/workflows/rag_workflow.py`) are explicit state machines
with an `ALLOWED_TRANSITIONS` map — there is no generic "set status to X"
path anywhere in the API. AI/automated code can reach `drafted`
(newsletter) or `pending_approval` (RAG) on its own; every transition
past that requires a human `performed_by`, enforced by role-checked
FastAPI dependencies (`app/auth.py`), not by the frontend hiding a
button.

RAG approval (`POST /candidates/{id}/rag/approve`) re-validates all five
verification checklist items server-side even though the API layer
already required reviewer input — the check is duplicated intentionally
in the service layer so it can never be bypassed by a future internal
caller. A technical ingestion failure produces `processing → failed`;
only an explicit human call produces `rejected` (spec #51).

## RAG adapter

`app/services/rag_adapter.py` defines a `RAGIngestionAdapter` interface.
`MockRAGIngestionAdapter` is deterministic and offline (default,
`RAG_ADAPTER=mock`); `HTTPRAGIngestionAdapter` is a real implementation
against a configurable endpoint, but **the actual CMT Veda RAG ingestion
contract has not been confirmed** — see IMPLEMENTATION_STATUS.md. Never
touches FAISS/embeddings/the RAG filesystem directly; only ever makes a
request through this interface.

## Worker

PostgreSQL-backed job queue (`app/worker/job_queue.py`): `SELECT ... FOR
UPDATE SKIP LOCKED` for safe concurrent claiming, exponential backoff on
retry, dead-lettering after `max_attempts`, and stale-lock recovery on
worker restart. Idempotent enqueueing via `dedupe_key` — this is also how
duplicate-run protection works (spec #34): `trigger_run` always enqueues
with `dedupe_key=f"collect_source:{source_id}"`, so a second trigger
while a job is already queued/running for that source is a no-op.

Job types: `collect_source`, `normalize_record`, `deduplicate_record`,
`analyse_candidate`, `generate_editorial_draft`, `maintenance`.
`collect_source` performs normalize+dedupe **inline per record** for
efficiency (a single run can involve hundreds of records); the standalone
`normalize_record`/`deduplicate_record` job types exist and are
dispatchable for reprocessing an individual record without forcing an
inefficient job-per-record queue for the common path.

Run the worker: `python -m app.worker.worker` (or via
`docker compose up discovery-worker`).

## API

FastAPI app (`app/main.py`), mounted at `/api/v1` by default
(`API_PREFIX` env var). Auto-generated interactive docs at `/docs` and
schema at `/openapi.json` once running — this **is** the frontend
contract the Lovable app will be built against.

Resource routers: `sources`, `runs`, `candidates` (filter/search/
paginate -- accepts either `limit`/`offset` or Veda-style `page`/
`page_size`), `analysis`, `editorial`, `newsletter` (includes
`newsletter/section`, `newsletter/publish-now`, and the public
`newsletter/published` feed), `rag`, `manual-discovery`, `audit`,
`document` (`GET /candidates/{id}/document`, `GET /documents/{id}` --
the original full-text record), `workspace` (`GET /candidates/{id}/
workspace` -- consolidated candidate detail for Veda-v1), plus
unversioned `/health` and `/readiness`. See `docs/API_CONTRACTS.md`
for the full, frozen contract Veda-v1 is built against.

Auth: bearer tokens mapped to roles (`discovery_administrator`,
`reviewer`, `service`) via `AUTH_ADMIN_TOKENS` / `AUTH_REVIEWER_TOKENS` /
`AUTH_SERVICE_TOKENS` env vars (comma-separated). This is a **placeholder**
until the real CMT Veda auth system is integrated — see
`app/auth.py`'s docstring for exactly what to replace.

## Manual discovery

`POST /api/v1/manual-discovery` runs through the **exact same**
normalize → deduplicate → candidate pipeline every collector uses —
there is no separate manual code path (see
`app/api/routers/manual.py` and `tests/test_manual_discovery.py`, which
call the same service functions directly to prove this).

## Running locally

### With Docker (recommended — includes PostgreSQL)

```bash
cp .env.example .env       # edit tokens/keys as needed
docker compose up --build
```

This starts PostgreSQL, runs `alembic upgrade head` (the `migrate`
service), then starts the API (`:8000`) and worker. Visit
`http://localhost:8000/docs`.

### Without Docker

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # point DATABASE_URL at a real Postgres instance
alembic upgrade head
uvicorn app.main:app --reload            # in one terminal
python -m app.worker.worker              # in another terminal
```

### Tests (no external dependencies — SQLite + mock providers)

```bash
pip install -r requirements.txt
pytest
```

Every test runs against an in-memory SQLite database and the mock
AI/RAG providers, so `pytest` needs no network access, no Postgres, and
no API keys.

## Configuration

See `.env.example` for the full list. Nothing is hard-coded — database
credentials, auth tokens, PubMed/ClinicalTrials.gov settings, the AI
provider and its API key, and the RAG adapter endpoint are all
environment variables.

## Known limitations / deliberate scope decisions

- **Manual-override precedence is candidate-level, not field-level.**
  Once `has_manual_override` is set, a new AI analysis pass won't
  overwrite *any* of the candidate's classification fields, even ones
  the admin didn't touch. A field-level override map is a reasonable
  future enhancement if editors need to accept some AI-proposed fields
  while overriding others.
- **JSON-array filters (`gene`/`subtype`/`topic`) on the candidates list
  endpoint** are applied in Python after a bounded SQL pre-filter, since
  portable JSON-containment queries differ between SQLite (tests) and
  PostgreSQL (production). In practice this means the candidate list
  endpoint fetches a filtered-but-unpaginated result set into Python
  before slicing for pagination. Fine at expected v1 data volumes; would
  need a SQL-side rewrite (e.g. JSONB `@>` filters, `LIMIT`/`OFFSET`
  pushed into the query) if candidate volume grows into the hundreds of
  thousands.
- **RAG HTTP adapter is a placeholder contract.** The real CMT Veda RAG
  ingestion endpoint shape was never provided/confirmed, per the
  instructions ("do NOT assume or invent the final ingestion endpoint").
  `HTTPRAGIngestionAdapter` implements a reasonable REST shape
  (`POST/GET/DELETE /ingest`) that should be adjusted once the real
  contract is confirmed — only that one class needs to change. It is
  marked `PROVISIONAL = True` and logs a warning on instantiation so
  this is impossible to miss operationally; do not point it at a real
  RAG deployment without confirming the wire format first.

See `IMPLEMENTATION_STATUS.md` for the full implemented/tested/deferred
breakdown.
