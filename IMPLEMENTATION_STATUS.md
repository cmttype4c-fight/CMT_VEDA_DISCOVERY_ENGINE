# Implementation Status

## Read this first: the environment this was built in

This engine was built and revised across three separate sessions in a
sandboxed container with **no network access** and **no pre-installed
Python packages** (no FastAPI, SQLAlchemy, pydantic, httpx, pytest --
nothing beyond the standard library). This was re-confirmed directly and
independently in every revision, including this one: `curl -sI
https://pypi.org` returns `403` with header `x-deny-reason:
host_not_allowed`, and `pip install`/`uv pip install` fail the same way.
Concretely, that means:

- Every `.py` file was verified with `python3 -m py_compile`, which
  catches syntax errors, but **the application has never actually been
  run**: no import of FastAPI/SQLAlchemy ever succeeded, `pytest` was
  never executed, `alembic upgrade head` was never run against a real
  database, and `docker compose up` was never built or started.
- Every fix described below, across all three revisions, was identified
  and verified by manually tracing the exact execution path through the
  source, matching it against each test's setup and assertions
  line-by-line -- not by running the test suite and reading a traceback.
  This is a weaker guarantee than an actual test run, and it stays true
  for this revision as well: **live external integrations (PubMed,
  ClinicalTrials.gov, a real RAG endpoint, real PostgreSQL) and a full
  `pytest` execution remain unverified.** Nothing in this document should
  be read as "tested" in the sense of "executed and observed to pass."
- I could not autogenerate the Alembic migration (`alembic revision
  --autogenerate`); `alembic/versions/0001_initial_schema.py` was
  **hand-written** to match the SQLAlchemy models column-for-column.
  Treat it as unverified until you run it against a real PostgreSQL
  instance. This revision cross-checked it column-by-column and
  index-by-index against the current models by hand (see the "Alembic
  migration vs. models" section below) -- that check is still a manual
  read, not a real `alembic upgrade head` run.
- PubMed and ClinicalTrials.gov collectors were written against my
  knowledge of their public API shapes and tested only against
  hand-built fixture data of the same shape -- never against the live
  services.

**Your first step should be a clean install and full test run** -- see
"What I'd verify first" at the bottom for exact commands.

---

## Fixes applied in Revision 3 (this revision)

Four targeted items, none of which change the frontend/API architecture,
patient/registry systems (this engine has never had any such
functionality -- spec #41), or the RAG system's external contract.

**1. `discovery_jobs.dedupe_key` concurrency-safe DB-level protection --
fixed.** The docstring on `DiscoveryJob.dedupe_key` already claimed
"enforced by a partial unique index in the migration," but the migration
only ever had a plain (non-unique) index -- the actual protection was
just `enqueue()`'s application-level check-then-insert, which has a
textbook race: two simultaneous callers can both pass the SELECT check
before either has inserted, creating two active jobs for the same
dedupe_key. Fixed by adding a real partial unique index,
`uq_jobs_dedupe_key_active`, on `discovery_jobs (dedupe_key)` WHERE
`status IN ('queued', 'running')` -- in both the SQLAlchemy model
(`app/models/job.py`, with `sqlite_where` so the SQLite test suite
exercises the same constraint) and the hand-written Alembic migration
(`postgresql_where`, `alembic/versions/0001_initial_schema.py`).
`enqueue()` (`app/worker/job_queue.py`) now performs its insert inside
its own SAVEPOINT (`db.begin_nested()`) and catches the resulting
`IntegrityError` on a lost race, re-querying for and returning the actual
winner instead of raising or creating a duplicate. Using its own
SAVEPOINT (rather than a plain `db.flush()`/`db.rollback()`) specifically
makes `enqueue()` safe to call from inside a caller's own nested
transaction -- e.g. the worker's per-record SAVEPOINT added in Revision 2
-- without disturbing it. Covered by four new tests in
`tests/test_worker_jobs.py`: a direct DB-constraint test (bypassing
`enqueue()` entirely), a simulated-lost-race recovery test, a
partial-index-scoping test (a completed job must not block a new active
one with the same key), and a nested-savepoint-safety test.

**2. `rag_workflow.verify()` restricted to `pending_approval` -- fixed.**
`verify()` previously mutated the five-flag checklist regardless of the
request's current status -- it could silently "verify" a request that
was never submitted (`not_selected`), already rejected, or already past
approval (`approved`, `queued`, `processing`, `indexed`, `failed`,
`removed`), which would either be meaningless or would retroactively
change what the checklist says about a decision that was already made.
Fixed by checking `req.status == RagStatus.pending_approval.value` before
any flag is touched, raising the existing `InvalidTransition` exception
(no new exception type introduced) otherwise. The `verify` API endpoint
(`app/api/routers/rag.py`) previously called `wf.verify()` directly,
un-wrapped -- an `InvalidTransition` would have surfaced as an unhandled
500, not a clean error. Fixed to route through the router's existing
`_handle()` helper, so it now correctly returns 409 Conflict, consistent
with every other state-machine violation in this API. Covered by seven
new tests in `tests/test_rag_workflow.py` (one per disallowed state, plus
a "flags genuinely untouched, not just an exception raised" check, plus
a reject-then-resubmit-then-verify-succeeds case) and one new API-level
test in `tests/test_api_end_to_end.py` confirming the 409 status code
end-to-end.

**3. RAG ingestion architecture -- reviewed, clarified, not redesigned.**
No behavior changed here (per the explicit instruction not to
redesign/add features or invent a new endpoint). What changed is
documentation, because the review surfaced a real architectural gap
worth being explicit about: `approve`/`retry` in
`app/api/routers/rag.py` call `adapter.submit()` synchronously, inline,
and `await` its result before the HTTP response returns. That's only
safe because the default adapter (mock) resolves instantly in-memory --
it is NOT how a production integration against a real, potentially slow
or unreliable RAG service should work, and the `queued`/`processing`
states in the `RagStatus` state machine exist precisely because the
original design intent was for a real ingestion call to be driven
asynchronously by the background worker, the same way `collect_source`
and `analyse_candidate` already are. Added an explicit architectural note
to that effect in the router's module docstring and both endpoints'
docstrings, and cross-referenced it from `app/services/rag_adapter.py`'s
module docstring, which now distinguishes two separate unconfirmed things
about `HTTPRAGIngestionAdapter`: (a) the wire contract itself, and (b)
the synchronous call site it would be plugged into. Moving `approve`/
`retry` to enqueue a worker job instead would be the correct production
fix, but is a genuine architecture change and stays out of scope here.

**4. ClinicalTrials.gov incremental collection (`since`) -- reviewed,
documented, deliberately not implemented.** `since` was accepted by
`ClinicalTrialsCollector.collect()` but never used to filter or sort the
query -- every run re-fetches the full matching study set. I considered
two ways to implement filtering: (a) an Essie field-scoped date-range
query embedded in `query.term`, or (b) a `sort=` parameter plus
client-side early-termination once results are no longer newer than
`since`. I did not implement either, and this was a deliberate choice,
not time pressure: I could not verify either mechanism's exact syntax
against the live ClinicalTrials.gov API v2 from this sandbox, and got it
wrong, either approach's LIKELY failure mode is not "less efficient" but
"silently skips genuinely updated trials" -- if the assumed sort order
doesn't hold, early-termination logic would stop collecting past a study
that only *looks* old due to ordering, missing real updates after it.
For a medical-data engine, silent data loss is a worse failure mode than
the current inefficiency of re-fetching unchanged studies (which
downstream deduplication already renders harmless -- unchanged
re-fetched studies are classified DUPLICATE and never create duplicate
candidates or re-run analysis). Documented this reasoning at length in
`app/collectors/clinicaltrials.py`'s module docstring and inline at the
top of `collect()`. Added `tests/test_clinicaltrials_incremental.py`,
which locks in the current documented behavior using `respx` to inspect
the actual request parameters sent (matching on host only, not an exact
path, so the test doesn't depend on incidental httpx URL-joining
behavior this build couldn't execute to confirm) -- if incremental
filtering is implemented later, this test will need a conscious update,
not silently break.

---

## Alembic migration vs. models cross-check (Revision 3)

Requested as part of this revision: a systematic, column-by-column
comparison of `alembic/versions/0001_initial_schema.py` against the
current SQLAlchemy models, done by extracting both sides
programmatically (column names, foreign keys, indexes, unique
constraints) and diffing them by hand -- not by running
`alembic upgrade head` and inspecting the result, which this sandbox
still cannot do.

**Result: all 12 tables match column-for-column** (name and order),
including the newly-added `discovery_jobs.uq_jobs_dedupe_key_active`
partial unique index from this revision's fix #1 above (present with
matching semantics -- `postgresql_where` in the migration,
`postgresql_where`+`sqlite_where` in the model -- in both places). All 11
foreign keys match on source table/column, target table/column,
`nullable`, and `unique`. Every other named index and unique constraint
matches by name and columns.

**One real naming inconsistency was found and fixed:** `discovery_audit_log.candidate_id`
had `index=True` set directly on the column in the model
(`app/models/audit.py`), which SQLAlchemy would auto-name differently
(e.g. `ix_discovery_audit_log_candidate_id`) than the migration's
explicitly-named `ix_audit_candidate_id` for the same column. Functionally
harmless (an index is an index regardless of name), but inconsistent
between the SQLite test schema and the Postgres migration schema. Fixed
by replacing the column-level `index=True` with an explicitly-named
`Index("ix_audit_candidate_id", "candidate_id")` in `__table_args__`,
matching the migration exactly. This was the only `index=True` usage
found anywhere in the models.

**One low-risk inconsistency was found and deliberately left as-is:**
`discovery_editorial_drafts.disclaimer` has a Python-side ORM default (a
long boilerplate disclaimer string, applied automatically by SQLAlchemy
whenever a `DiscoveryEditorialDraft` is constructed without explicitly
setting it) but no matching `server_default` in the migration. Every
other short/simple Python-side default in the models does have a
mirrored `server_default` in the migration; this one text field is the
sole exception. This cannot cause a problem through the application
(every insert into this table goes through the ORM, in
`app/services/intelligence/editorial_service.py`, which always supplies
the default), so it's not a bug the app can hit -- it would only matter
for a hypothetical raw-SQL insert bypassing the ORM entirely, which
nothing in this codebase does. Not fixed, because doing so would mean
duplicating a multi-sentence string as a literal inside the migration,
creating a real risk of the two copies silently drifting out of sync
over time -- a worse outcome than the current, harmless gap.

**Caveat, same as everywhere else in this document:** this is a static
comparison of two Python source files, not a real
`alembic upgrade head` run against PostgreSQL. It would still catch
column/FK/index mismatches, but it cannot catch things that only
PostgreSQL itself would reject or behave differently on (type coercion
edge cases, actual constraint enforcement, migration ordering issues
that only manifest against a real running database).

---

## Fixes applied in Revision 2

**`tests/test_worker_jobs.py::test_one_bad_record_does_not_abort_the_whole_source_run`
-- fixed.** The per-record failure-isolation mechanism in
`handle_collect_source` (`app/worker/handlers.py`) previously caught a
per-record exception and called a full `db.rollback()`. A full
`Session.rollback()` in SQLAlchemy expires every object currently in the
session -- including `run` and `source`, which the function keeps using
for the rest of the loop and in its `finally` block. Reading/writing
attributes on an expired object generally still works correctly
(assignment doesn't require a reload first), but relying on that
expire/reload interaction to behave exactly as needed in every case is
fragile, and was the most likely source of the reported failure. Rather
than continuing to reason about that interaction in the abstract, the
fix removes the ambiguity structurally:

- Each record's DB work is now wrapped in its own SAVEPOINT
  (`db.begin_nested()`) instead of relying on a full session rollback for
  isolation. If a record fails, SQLAlchemy rolls back only to that
  SAVEPOINT -- undoing just that record's partial work -- and `run`,
  `source`, and everything committed for earlier records are never
  touched or expired at all. This is the standard SQLAlchemy pattern for
  "isolate one sub-operation's failure without disturbing the
  surrounding transaction," and it removes the failure mode entirely
  rather than working around one specific manifestation of it:

  ```
  bad record
      |
      v
  record error recorded   (SAVEPOINT rolled back; run/source untouched)
      |
      v
  next record continues
      |
      v
  source run does not abort
  ```

- Run-level `stats` counters are now only incremented *after* the
  SAVEPOINT block completes without raising, never inside it -- a
  Python-level counter increment is not undone by a SAVEPOINT rollback,
  so incrementing inside the block (as the previous code effectively did)
  risked inflating stats for work that was actually rolled back on a
  partial failure.
- SAVEPOINT support on SQLite requires disabling pysqlite's own implicit
  transaction handling (a well-documented SQLAlchemy/pysqlite
  interaction, not something specific to this codebase). Added a small
  `enable_sqlite_savepoints()` helper in `app/database.py`, called
  unconditionally at engine-creation time -- it's a no-op for any
  non-SQLite dialect, so PostgreSQL in production is unaffected -- and
  also applied to the SQLite test engine in `tests/conftest.py`.
- **Source-level failure isolation** (one source failing to collect at
  all must not affect other sources) was already structurally
  guaranteed and required no change: each source's `collect_source` job
  runs as its own job, claimed and executed with its own database
  session (`app/worker/worker.py`'s `session_scope()` per job).
  Re-verified this by re-reading `Worker._run_job()` end-to-end.

**On the 12 errors attributed to missing `structlog`/`feedparser`:**
both are already correctly pinned in `requirements.txt`
(`structlog==24.4.0`, `feedparser==6.0.11`) and were in the previous
revision too -- nothing changed here. If a test run reports them as
missing, the most likely explanation is a partial or stale install
rather than a gap in the dependency list. Try a **completely fresh
virtualenv**:

```bash
python3.11 -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
pytest -v
```

If a clean install in a fresh venv still can't resolve `structlog` or
`feedparser`, that indicates a real packaging/environment problem worth
reporting back (e.g. a corporate proxy blocking certain packages, or a
Python version incompatibility) -- but it would not be an application
defect in this codebase.

**Re-verified, unchanged, still correct (re-read end-to-end this pass):**
- Deduplication `DUPLICATE` vs `UPDATED` fix from Revision 1
  (`normalize_text()` shared between matching and hashing in
  `app/collectors/base.py` / `app/services/deduplication.py`).
- RAG `approve()` transition-vs-verification ordering fix from Revision 1
  (`app/services/workflows/rag_workflow.py`).
- `newsletter_workflow.py` re-checked again for the same class of
  ordering bug -- still no compound preconditions there, so still no
  analogous issue.

## Fixes applied in Revision 1

Two concrete bugs were reported as failing tests and were fixed:

1. **DUPLICATE vs UPDATED misclassification** (`app/collectors/base.py`,
   `app/services/deduplication.py`). Root cause:
   `NormalizedRecord.content_hash()` hashed the *raw* title, while the
   deduplication matcher's fuzzy-title fallback compared *normalized*
   titles (lowercased, punctuation stripped). Two records the matcher
   correctly recognized as "the same paper" (e.g. differing only in
   title casing/punctuation) could still produce different content
   hashes, so `deduplicate()` classified them as `UPDATED` instead of
   `DUPLICATE`. Fix: both the matcher and the hash now call the same
   `normalize_text()` function (moved to `app/collectors/base.py` as the
   single source of truth for "is this the same text"), so anything the
   matcher considers identical also hashes identically. Status-change
   detection (e.g. a ClinicalTrials.gov trial moving from `RECRUITING`
   to `ACTIVE_NOT_RECRUITING`) is unaffected, since that comparison is on
   the raw `status` field, not the title. Covered by
   `tests/test_deduplication.py::test_dedupe_by_normalized_title_fallback`.

2. **RAG invalid-transition vs verification ordering**
   (`app/services/workflows/rag_workflow.py`). Root cause: `approve()`
   checked `all_checks_passed` (which defaults to `False`) *before*
   checking whether the request was even in a state that could be
   approved. Calling `approve()` on a candidate that was never submitted
   to the RAG path raised `VerificationIncomplete` instead of the
   correct `InvalidTransition` (its RAG request is still `not_selected`,
   which can't move to `approved` regardless of verification flags).
   Fix: the state-transition check now runs first; `VerificationIncomplete`
   is only raised once the request is confirmed to be in a state
   (`pending_approval`) where approval is even possible. Covered by
   `tests/test_rag_workflow.py::test_cannot_approve_without_submission`.

As part of the same pass:
- Reviewed every other state machine (`newsletter_workflow.py`) for the
  same class of "compound precondition checked in the wrong order" bug.
  It only has single transition-validity checks, no compound checks like
  RAG's five-flag verification -- no analogous issue found.
- Removed a leftover unused `uuid` import in `deduplication.py`.
- Re-traced every other test file (candidate engine, intelligence,
  editorial, audit, manual discovery, authorization, worker jobs, API
  end-to-end) line-by-line against the current implementation.
- Strengthened `HTTPRAGIngestionAdapter`'s provisional status: it now
  logs a warning on instantiation and carries an explicit `PROVISIONAL`
  class attribute, in addition to its existing docstring warning. Its
  interface and behavior are unchanged -- this is a clarity/observability
  change, not new functionality.

---

## Status table

| Component | Implemented | Tested | External Dependency | Status |
|---|---|---|---|---|
| PostgreSQL schema (models) | Yes | No (SQLite-equivalent schema exercised via tests, not real Postgres) | PostgreSQL server | Implemented, not executed against real Postgres |
| Alembic migration | Yes (hand-written, not autogenerated) | No | PostgreSQL server | Implemented, not executed |
| Source Registry | Yes | Yes (via API/service tests, SQLite) | — | Implemented, unit-tested |
| PubMed collector | Yes (esearch/efetch client + XML parser) | Parser only (fixture XML, offline) | NCBI E-utilities network access, optional API key | Parsing logic tested; live HTTP path never executed |
| ClinicalTrials.gov collector | Yes (API v2 client + JSON parser) | Parser only (fixture JSON, offline); `since`'s deliberate no-op behavior locked in by `tests/test_clinicaltrials_incremental.py` | clinicaltrials.gov network access | Parsing logic tested; live HTTP path never executed; incremental (`since`) filtering deliberately NOT implemented -- documented in Revision 3, see that section for reasoning |
| Generic RSS collector | Yes | No dedicated test written | Feed URL network access | Implemented, not executed |
| Other collectors (NIH/NINDS, RDCRN, CMTRF, CMTA, HNF, journals, institutions, conferences) | Interface only (`NotImplementedCollector` placeholder, fails loudly) | N/A | N/A | Deferred, by design (spec #8 allows this for v1) |
| Normalization | Yes | Yes (unit tests) | — | Implemented, unit-tested |
| Deduplication (DOI/PMID/CTID/URL/title, NEW/DUPLICATE/UPDATED) | Yes | Yes (unit tests incl. status-change and idempotency cases) | — | Implemented, unit-tested; DUPLICATE-vs-UPDATED bug fixed in Revision 1 |
| Candidate Engine (creation, refresh, override precedence) | Yes | Yes (unit tests) | — | Implemented, unit-tested |
| Veda Intelligence (rules stage, mock AI provider, orchestration, gene-hallucination guard, prompt-injection wrapping) | Yes | Yes (unit tests, mock provider) | — | Implemented, unit-tested |
| Veda Intelligence (Anthropic provider) | Yes (HTTP client against documented `/v1/messages` shape) | No | Anthropic API network access + `ANTHROPIC_API_KEY` | Implemented, not executed |
| Editorial Generation | Yes | Yes (unit tests, mock provider) | — | Implemented, unit-tested |
| Newsletter Workflow (state machine, human-authority gating) | Yes | Yes (unit tests incl. invalid-transition cases) | — | Implemented, unit-tested |
| RAG Workflow (verification checklist, approval gating, failed-vs-rejected distinction) | Yes | Yes (unit tests) | — | Implemented, unit-tested; ordering bug fixed in Revision 1; `verify()` now restricted to `pending_approval` in Revision 3 |
| RAG Adapter — mock | Yes | Yes (unit + API tests) | — | Implemented, tested |
| RAG Adapter — HTTP | Yes (placeholder contract, per instructions not to invent the real one) | No | Real CMT Veda RAG endpoint (contract TBD), network access | **PROVISIONAL** on two counts (Revision 3 review): the wire contract is unconfirmed AND the current call site (`approve`/`retry`) invokes it synchronously inline, which is documented as not the intended production design -- deferred pending both a confirmed contract and a move to worker-driven async ingestion |
| Worker (job queue, claim/complete/fail, backoff, dead-letter, crash recovery, per-record failure isolation, concurrency-safe dedupe_key) | Yes | Yes (unit tests) | PostgreSQL (for real concurrent locking; SQLite path used in tests degrades safely to single-connection claiming) | Implemented, unit-tested; per-record isolation rewritten to use SAVEPOINTs in Revision 2; `dedupe_key` now has a real partial unique index (not just an application-level check) as of Revision 3 |
| Scheduling (`due_sources`, `trigger_run`, "run now") | Yes | Indirectly (via idempotent-run-trigger API test) | — | Implemented, lightly tested |
| Audit Log | Yes | Yes (unit tests) | — | Implemented, unit-tested |
| APIs (all resource routers, pagination, filtering, search, auth) | Yes | Yes (integration tests via FastAPI TestClient + SQLite) | — | Implemented, tested against in-memory DB — **never run against a real server process** |
| Authorization hooks | Yes (bearer-token-to-role mapping; placeholder for real CMT Veda auth) | Yes (unit tests for 401/403 cases) | Real CMT Veda auth system (future integration) | Implemented, unit-tested; explicitly a placeholder |
| Docker (Dockerfile, docker-compose.yml, no Redis) | Yes | No (never built/run) | Docker | Implemented, not executed |
| Tests (pytest suite) | Yes (14 test files, golden dataset, fixture responses) | Written but **never executed** by the author (no pytest available in the build sandbox) | pytest + dependencies | Implemented, **not run by the author — please run `pytest` yourself and treat that as the real gate, not this document** |
| Golden dataset (10 fixture items per spec #46) | Yes | Used throughout the test suite | — | Implemented |

**This engine is not production-ready simply because it compiles.**
`py_compile` only proves the syntax is valid Python -- it says nothing
about whether the code behaves correctly at runtime, whether the
database migration actually applies, whether the collectors parse real
API responses, or whether the full test suite genuinely passes. Every
fix in this document was reasoned through carefully, but "carefully
reasoned" is not a substitute for "executed and green." Treat this
engine as a thorough, disciplined draft that needs a real test run,
a real `alembic upgrade head` against Postgres, and ideally a real
PubMed/ClinicalTrials.gov call before any production judgment is made.

## Deferred by design (explicitly out of scope for v1, per the spec)

- Additional source collectors beyond PubMed, ClinicalTrials.gov, and
  generic RSS (NIH/NINDS, RDCRN, CMTRF, CMTA, HNF, journal-specific
  scrapers, controlled webpage extraction for feed-less orgs).
- Structured-feed and controlled-webpage-extraction collection methods
  (spec #8 explicitly allows deferring these for v1).
- The real RAG ingestion contract (spec #25 explicitly says not to
  assume or invent it) -- the HTTP adapter remains explicitly provisional
  on both the contract and the synchronous call site (Revision 3).
- Moving RAG ingestion (`approve`/`retry`) from an inline synchronous
  adapter call to an asynchronous worker-driven job -- reviewed and
  documented as the correct production direction in Revision 3, but is a
  genuine architecture change and was left out of scope.
- ClinicalTrials.gov incremental collection via `since` -- reviewed in
  Revision 3; deliberately not implemented because neither candidate
  mechanism (an Essie date-range query, or a `sort` parameter plus
  client-side early termination) could be verified against the live API
  from this sandbox, and getting either wrong risks silently missing
  updated trials rather than just being inefficient.
- Field-level (as opposed to candidate-level) manual override precedence.
- SQL-side (as opposed to Python-side) JSONB array-membership filtering
  for very large candidate tables.
- Real CMT Veda authentication integration (placeholder hooks only, by
  design — spec #40 says the real system "will be integrated later").

## What I'd verify first if I were you

1. **Clean install, full suite:**
   ```bash
   python3.11 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install -r requirements.txt
   pytest -v
   ```
   This is the single most valuable next step; it will catch anything
   `py_compile` couldn't (import errors, wrong SQLAlchemy/Pydantic API
   usage at runtime, subtler logic bugs). Pay particular attention to:
   - `pytest tests/test_worker_jobs.py -v` (Revision 2's record-isolation
     fix, and Revision 3's four new `dedupe_key` concurrency tests --
     `test_db_rejects_duplicate_active_jobs_for_same_dedupe_key`,
     `test_completed_job_does_not_block_new_active_job_with_same_dedupe_key`,
     `test_enqueue_recovers_gracefully_from_lost_dedupe_race`,
     `test_enqueue_still_works_normally_inside_a_savepoint`)
   - `pytest tests/test_rag_workflow.py -v` (Revision 1's RAG ordering
     fix, and Revision 3's seven new `verify()` state-restriction tests)
   - `pytest tests/test_api_end_to_end.py -v` (includes the new
     `test_rag_verify_rejected_when_not_pending_approval` 409 check)
   - `pytest tests/test_deduplication.py -v` (Revision 1's dedup fix --
     unchanged this revision, re-confirm no regression)
   - `pytest tests/test_clinicaltrials_incremental.py -v` (Revision 3's
     new regression tests locking in the documented `since` behavior)
2. `docker compose up --build` against a fresh Postgres and confirm
   `alembic upgrade head` actually succeeds, including the new
   `uq_jobs_dedupe_key_active` partial index (the hand-written migration
   is the highest-risk artifact in this delivery).
3. A real PubMed API call (an `esearch`/`efetch` pair) to confirm the XML
   shape still matches what `parse_pubmed_xml` expects — NCBI's schema is
   stable but I could not verify against it live.
4. A real ClinicalTrials.gov `/studies` call for the same reason, and to
   inform whether incremental filtering (deferred above) can be safely
   implemented later.
5. If/when a real RAG ingestion contract is confirmed, revisit the
   architectural note in `app/api/routers/rag.py` before pointing
   `HTTPRAGIngestionAdapter` at it in production.
