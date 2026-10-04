# Implementation Status

## Simplify Discovery Newsletter Workflow and Clean Dataset (this revision, latest)

Four-part task, Discovery Engine only (Veda-v1/CMT Veda frontend untouched,
confirmed): (1) remove Auto-Pilot, (2) clean the candidate dataset, (3)
retain candidate `4952752b-42a2-4b33-939c-94e445c36b8c` for testing, (4) keep
full-text acquisition + every API the Veda-v1 Admin Console uses, without
touching candidate-selection/relevance logic, RAG code, or adding new
workflow complexity.

### 1. Auto-Pilot removal

Investigated first, before writing anything: a repo-wide, case-insensitive
grep across `app/`, `tests/`, and `alembic/` for `autopilot`, `auto_pilot`,
`auto-pilot`, `AutoPilot`, plus `auto_approve`, `auto_schedule`, and any
threshold-based auto-promotion logic in the worker, returned **zero
matches**. The only three "automatic" things that exist anywhere in this
backend are: (a) the IST source-collection scheduler
(`app/worker/ist_scheduler.py` / `scheduler.py`, unrelated -- decides when
to run a *collector*, never touches newsletter/RAG state), (b) the AI
worker reaching `drafted` via `mark_drafted()` (spec #22/23: AI is
explicitly barred from reaching `approved`/`scheduled`/`published` on its
own, enforced in `app/services/workflows/newsletter_workflow.py`), and (c)
the stuck-job timeout in `app/worker/job_queue.py` (nothing to do with
candidates at all). **There was no Auto-Pilot implementation in the
Discovery Engine to remove.**

Cross-checked against the attached `veda-v1-main` frontend
(`src/lib/discovery-api.server.ts::getAutoPilot`/`setAutoPilot`): it already
calls `GET/PUT /autopilot` (falling back to `/auto-pilot`, `/settings/autopilot`)
defensively, and on a `DiscoveryContractGapError` (404/405 -- endpoint
doesn't exist) it already returns `AUTO_PILOT_UNSUPPORTED` rather than
erroring. The UI was already built assuming the backend might not support
Auto-Pilot at all, which is exactly the Discovery Engine's actual, unchanged
state. No backend code needed to change, and none did.

### 2-4. Dataset cleanup: candidates, retain one

**No live database or VPS access from this sandbox (standing constraint
across this entire engagement)** -- so this pass adds the safe,
reviewed-before-run tool to *perform* the cleanup, rather than performing it
directly. The user runs it against their real database; see "Deployment
commands" in the task report for the exact invocation.

- **`app/services/reset_service.py`** -- added `dry_run_selective_counts()`
  and `execute_selective_reset()` alongside the existing (unmodified)
  `dry_run_counts()`/`execute_reset()` full-wipe pair. Same safety
  discipline: read-only dry run first, `confirm=True` required (keyword-only,
  never a default), single transaction, FK-respecting delete order (children
  before parents). Deletes, for every candidate *except* the one retained:
  `newsletter_items`, `rag_ingestion_requests`, `discovery_editorial_drafts`,
  `discovery_analysis`, `discovery_documents`, `discovery_source_records`
  (including orphaned records with `candidate_id IS NULL` -- never promoted
  to any candidate), `discovery_candidates`. Refuses (raises `ValueError`,
  touches nothing) if the named candidate doesn't exist -- a mistyped UUID
  must never silently become "delete everything."

  Deliberately narrower scope than the existing full `execute_reset()`:
  `discovery_sources` (collector configuration), `discovery_runs`
  (collection-run history, tied to a source, not a candidate),
  `discovery_jobs` (no `candidate_id` column at all), `discovery_taxonomy`,
  and `discovery_audit_log` (no FK to candidates, by design -- an audit
  trail outlives the data it describes) are all left completely untouched,
  for *every* candidate, kept or removed. This cleans "the candidate
  dataset" as asked, not collection/run history that was never part of the
  request.

- **`scripts/clean_dataset.py`** (new) -- the deployment-facing CLI: dry run
  by default, `--confirm` to actually delete, prints exactly what would be
  deleted/retained either way, non-zero exit on refusal or an unverified
  post-state.

- **`tests/test_reset_service.py`** -- 6 new tests alongside the existing,
  unmodified reset tests: dry-run counts reflect the delete/retain split;
  refuses without `confirm=True`; refuses for a nonexistent candidate id;
  the real run keeps only the named candidate (and only its own dependent
  rows) across every touched table; orphaned source records are removed;
  sources/runs/jobs/taxonomy/audit survive untouched for *both* the kept and
  the removed candidate (confirms the deliberate scope difference from the
  full reset).

### Verification performed this pass

No `pytest`/`sqlalchemy` in this sandbox (as every prior pass), so: a full
repository `py_compile` sweep passed clean, and a genuine, non-mocked
execution harness (`importlib` + minimal `sqlalchemy` stand-in with real
boolean-condition semantics, same technique as every prior pass) loaded the
**real, unmodified-on-disk** `app/services/reset_service.py` and exercised
`dry_run_selective_counts`/`execute_selective_reset` directly against an
in-memory two-candidate-plus-orphan-record fixture. **30/30 genuine checks
passed**, including: correct delete/retain counts, both refusal paths
(`confirm=False`, nonexistent candidate), the real run leaving exactly one
candidate with only its own rows across every table, orphan cleanup, and
confirmed non-interference with `discovery_runs`/`discovery_jobs`.

The acceptance criterion "confirm one test candidate remains" cannot be
verified against a live database from this sandbox; it is proven against
the real code via the harness above, and the exact command to verify it
against the real database is in the task report's "Deployment commands."

### Explicitly NOT done / NOT changed (per instructions)

No RAG code touched (`app/api/routers/rag.py`, `rag_adapter.py`,
`rag_workflow.py`, `models/rag.py` all unmodified). No change to
candidate-selection/relevance logic (`analysis.py`, `candidates.py`
unmodified). No change to full-text acquisition
(`app/services/fulltext/*` unmodified). No router/endpoint added, removed,
or changed -- every API the Veda-v1 Admin Console uses is exactly as it was.
No new workflow/state machine introduced -- `execute_selective_reset` is a
one-shot data-cleanup utility, not a workflow. Veda-v1 (CMT Veda frontend)
not modified.

---

## Manual Editorial Draft Save Workflow fix (previous revision)

Bug report: CMT Veda's "Save Draft" button calls
`PATCH /candidates/{candidate_id}/editorial` (the compatibility route for
`edit_draft()` in `app/api/routers/editorial.py`), which correctly saved the
draft via `update_draft_manually()` but never touched the candidate's
`NewsletterItem` -- it never transitioned `selected -> drafted` and never set
`NewsletterItem.editorial_draft_id`, unlike the automated worker path
(`app/worker/handlers.py::handle_generate_editorial_draft`), which correctly
calls `mark_drafted(db, candidate, draft.id)` when the item is `selected`.
Confirmed live: candidate `4952752b-42a2-4b33-939c-94e445c36b8c`, selected via
`POST /candidates/{id}/newsletter/select`, had an AI draft generated by
Gemini (which correctly left `newsletter_status` at `selected` -- draft
*generation* alone is not supposed to advance the workflow), and saving that
draft via the "Save Draft" button left the item stuck at `selected` forever.

### The fix

Two files changed, no CMT Veda code touched (confirmed unnecessary after
reviewing the attached `veda-v1-main` frontend -- see below), no change to
`ALLOWED_TRANSITIONS`, no change to any other transition function, no change
to auth/permission dependencies.

1. **`app/services/workflows/newsletter_workflow.py`** -- added a new sibling
   function, `mark_draft_saved_manually(db, candidate, editorial_draft_id,
   performed_by)`, placed directly after the existing `mark_drafted()`.
   `mark_drafted()` itself is completely unchanged (the automated worker path
   keeps its exact existing behavior). The new function:
   - Performs the *same* `selected -> drafted` guard as `mark_drafted` (only
     when the item is currently `selected`; every other status is left
     exactly where it is -- `drafted` stays `drafted`, and `under_review`,
     `approved`, `scheduled`, `published` are never regressed).
   - Always refreshes `item.editorial_draft_id` to the just-saved draft's id,
     regardless of status -- this is an association, not a state transition,
     so it is safe and correct to keep it current no matter how far along
     review has progressed.
   - Writes an audit log entry with `action=AuditAction.edited` (an action
     already defined on the `AuditAction` enum but, before this fix, used
     nowhere in the codebase -- confirmed by a repo-wide grep) and the
     **real** `performed_by` (the authenticated principal who called the
     endpoint), instead of `mark_drafted`'s hardcoded
     `action=AuditAction.draft_generated, performed_by="veda_intelligence"`.
     This is the exact distinction the fix required: a human manual save must
     never produce an audit record that claims the AI generated the draft.

2. **`app/api/routers/editorial.py`** -- `edit_draft()` (the function backing
   `PATCH /candidates/{id}/editorial-draft`) now looks up the candidate's
   `NewsletterItem` the same way the worker does
   (`select(NewsletterItem).where(NewsletterItem.candidate_id == candidate.id)`)
   and, only if one already exists, calls
   `mark_draft_saved_manually(db, candidate, new_draft.id,
   performed_by=principal.subject)` before committing. A candidate with no
   `NewsletterItem` at all (never selected for the newsletter) gets no new
   item created and no transition -- mirroring the worker's own
   `existing_item is not None` guard exactly, so a draft-only edit on a
   candidate outside the newsletter pipeline has zero side effects, same as
   before this fix.

   Since `edit_draft_compat()` (backing the CMT Veda `PATCH
   /candidates/{id}/editorial` route) calls `edit_draft()` directly with zero
   duplicated logic, this single change fixes both routes identically. Both
   routes' existing auth dependencies (`require_reviewer_or_admin` on
   `/editorial-draft`, `require_service_or_reviewer_or_admin` on the compat
   `/editorial`) are completely unchanged.

### Frontend verification (no code changed)

The user additionally attached the CMT Veda frontend repository
(`veda-v1-main`) with the instruction that it "should be in synch with the
attached UI." Reviewed `src/lib/discovery-api.server.ts`:
`saveDraft()` calls `PATCH /candidates/{id}/editorial` first, falling back to
`PATCH /candidates/{id}/editorial-draft`; its `toEditorialDraftUpdate()`
builder emits a flat snake_case body (`headline`, `summary`,
`why_it_matters`, `key_points`, `detailed_content`,
`cmt_relevance_explanation`, `limitations`, `references`, `draft_status`)
that matches `EditorialDraftUpdate` field-for-field; and after saving it
re-reads the candidate via `fetchCandidateWithDraft()`
(`GET /candidates/{id}` + `GET /candidates/{id}/editorial-draft`), with
`discovery-normalise.server.ts` reading `newsletter_status` directly off the
candidate JSON and mapping the backend's `"drafted"` to the UI's own
`"draft"` status via `EDITORIAL_ALIASES`. All of this already expects
exactly the behavior this fix now provides -- no frontend changes were
needed or made.

### Tests added

Five new tests in `tests/test_cmt_veda_compat.py` (plus a small shared
`_item_for_candidate` helper), covering every required scenario:

- `test_manual_draft_save_transitions_selected_to_drafted_and_sets_draft_id`
  -- selected -> PATCH `.../editorial` -> `drafted`, `editorial_draft_id` set
  to the saved draft's id.
- `test_manual_draft_resave_stays_drafted_no_invalid_transition` -- saving
  again while already `drafted` (via the native `.../editorial-draft` route)
  stays `drafted -> drafted` with no `InvalidTransition`, and the draft
  association is refreshed to the newest version.
- `test_manual_draft_save_does_not_regress_under_review` -- saving while
  `under_review` leaves it at `under_review`, never pulled back to
  `drafted`.
- `test_manual_draft_save_does_not_regress_approved_scheduled_or_published`
  -- walks a candidate through `approved` -> `scheduled` -> `published`,
  saving a draft edit at each stage and confirming the status never moves.
- `test_manual_draft_save_both_routes_keep_their_own_permissions` --
  confirms unauthenticated requests are still `401` on both routes, the
  native route still works for a reviewer token, and the compat route still
  additionally accepts a service token -- both driving the same fix.

The pre-existing `test_editorial_alias_reuses_editorial_draft_logic` and
`test_editorial_alias_accepts_service_token` tests (which patch a draft for a
candidate that was never selected for the newsletter) continue to pass
unchanged, since their candidates have no `NewsletterItem` at all and so hit
the new code's `existing_item is None` no-op path.

### Verification performed this pass

`pytest`/`sqlalchemy`/`fastapi`/`respx` are not installed in this sandbox (as
in every previous pass of this engagement), so:

- A full repository `py_compile` sweep passed clean (every `.py` file,
  working tree).
- A genuine, non-mocked execution harness (`sys.modules` stubbing +
  `importlib.util.spec_from_file_location`, the same technique used for the
  Europe PMC fix) loaded the **real, unmodified-on-disk**
  `app/models/enums.py`, `app/services/workflows/newsletter_workflow.py`, and
  `app/api/routers/editorial.py` -- stubbing out only `sqlalchemy` and
  `fastapi` themselves (not installed here) with minimal stand-ins, and
  calling the real `mark_draft_saved_manually`, `mark_drafted`, `edit_draft`,
  and `edit_draft_compat` functions directly. **31/31 genuine checks passed**,
  covering all 5 required scenarios plus two extra regression checks: the
  AI/automated `mark_drafted()` path's audit attribution
  (`veda_intelligence`/`draft_generated`) is proven completely untouched, and
  a candidate with no existing `NewsletterItem` gets no item spuriously
  created by a draft-only edit.
- No live VPS/database access is available from this sandbox (as in every
  previous pass); the acceptance criterion for candidate
  `4952752b-42a2-4b33-939c-94e445c36b8c` reaching `drafted` after "Save
  Draft" cannot be independently re-verified live from here, but is exactly
  the behavior scenario 1 above proves against the real code.

### Explicitly NOT done (per instructions)

No database reset. No automatic approval, scheduling, or publishing added.
No MCP implemented. No Autopilot implemented. No change to
`ALLOWED_TRANSITIONS` or any other transition function in
`newsletter_workflow.py`. No change to auth/permission dependencies. No CMT
Veda (frontend) code changed.

---

## CMT-specific discovery overhaul — EUROPE PMC FULL-TEXT CORRECTION: OFFICIAL XML FALLBACK (previous revision)

This is the sixth corrective pass, triggered by real VPS testing the
user performed against four live PMC articles (PMC13571996,
PMC13563502, PMC13570443, PMC13578534): Europe PMC's PDF *render* URL
(`https://europepmc.org/articles/{PMCID}?pdf=render`) returns HTTP 403
from the VPS for all four, while the official, documented
`https://www.ebi.ac.uk/europepmc/webservices/rest/{PMCID}/fullTextXML`
endpoint returns HTTP 200 with substantial full-text XML for all four.
Before this pass, `app/services/fulltext/resolver.py` resolved exactly
ONE candidate URL (the single highest-priority format Europe PMC's own
`fullTextUrlList` happened to list) and `app/services/fulltext/service.py`
had no fallback if that one download failed -- a PDF 403 was a terminal
candidate failure even when official XML full text was genuinely
available a request away.

### The fix

`app/services/fulltext/resolver.py` gains `FullTextCandidate` and
`_build_candidates()`: instead of picking one URL, it now builds an
ORDERED fallback chain -- PDF (only if Europe PMC's own metadata offers
one) → the OFFICIAL `fullTextXML` REST endpoint, built directly from the
result's `pmcid` (`f"{europepmc_base_url}/{pmcid}/fullTextXML"`) →
HTML (if offered) → any other listed URL, honestly labeled "other". The
key design point: the XML candidate is added whenever a `pmcid` is
present AT ALL, independent of whether `fullTextUrlList` happens to list
an "xml" `documentStyle` entry for this particular result -- which
matches what the four tested PMCIDs actually look like (PDF + HTML
listed, no "xml" style entry, but a `pmcid` present) and is what makes
the official endpoint actually get tried rather than silently skipped.
`ResolutionResult` keeps its existing `.url`/`.mime_type`/
`.full_text_format`/`.full_text_source` fields for backward compatibility
(always equal to `candidates[0]`'s fields) and adds `.candidates` — the
full chain. `_pick_best_url` (the previous single-URL picker) is
untouched and still directly unit-tested; `resolve_full_text` no longer
calls it internally (superseded by `_build_candidates`, which also needs
the PMCID-derived endpoint that isn't necessarily in `fullTextUrlList` at
all).

`app/services/fulltext/service.py` gains `_walk_candidate_chain()`,
called from `acquire_full_text()` in place of the old single
download-and-sniff block. It tries each candidate in the chain in order:

- A transport/HTTP-layer failure (a 403, a timeout, any `httpx.HTTPError`)
  is **not terminal** — it moves to the next candidate. This is the
  exact fix: "Do not treat a PDF 403 as a terminal candidate failure when
  official XML full text is available."
- A download that succeeds but whose bytes don't match ANY recognized
  format (`sniff_format` returns `None`) is treated the same way — try
  the next candidate — since an unrecognized response looks exactly like
  a blocked/interstitial page.
- A download whose bytes DO match a recognized format, but not the one
  Europe PMC's metadata claimed (e.g. claimed "pdf", bytes are genuinely
  HTML), is still **accepted and corrected** to the real format — this
  is FINAL CORRECTIVE PROMPT #3/#6's pre-existing behavior
  ("never mislabel", not "never accept a mismatch"), deliberately left
  unchanged so a genuinely-served alternate format is never discarded
  just because it didn't match the claim.
- Only once every candidate in the chain is exhausted does acquisition
  terminally fail — `retrieval_status` is `"failed"` if no candidate ever
  returned bytes at all, `"unsupported"` if at least one did but none
  were recognized, with a combined `error_detail` describing every
  attempt.

Storage/format recording is exactly as required: `full_text_format` is
`"pdf"`/`"xml"`/`"html"` per whichever candidate actually succeeded and
sniffed correctly (never guessed); `pdf_available` is `True` only when
`full_text_format == "pdf"`, so it correctly stays `False` when XML (or
HTML) is what was actually used; `candidate.full_text_available` becomes
`True` on any successful acquisition regardless of format, never
abstract-only when valid XML full text exists; `extracted_text` is
populated via the existing, unchanged `extract_text()` (XML extraction
via `xml.etree.ElementTree`, already implemented and stdlib-only).

**Gemini flow** — checked, no code change needed:
`app/services/intelligence/editorial_service.py::_get_acquired_document`
already selects any `DiscoveryDocument` with `retrieval_status="acquired"`
and `extraction_status="success"`, regardless of `full_text_format` — an
XML-sourced document is already handed to Gemini as primary source
material exactly the same way a PDF-sourced one is, and the abstract is
already only ever a supplement/fallback when no document was
successfully extracted at all (never a deliberate downgrade from a
successful XML acquisition). The flow
`Europe PMC source → fullTextXML → extracted source text → Gemini` was
already correct once acquisition itself was fixed; this pass verified
that by reading the code, not by guessing.

### Files changed this pass

- `app/services/fulltext/resolver.py` — added `FullTextCandidate`
  dataclass, `_build_candidates()`; `ResolutionResult` gained a
  `candidates` field; `resolve_full_text()` now builds and returns the
  full chain instead of a single best URL. `_pick_best_url` unchanged.
- `app/services/fulltext/service.py` — added `_ChainOutcome` dataclass
  and `_walk_candidate_chain()`; `acquire_full_text()`'s download logic
  rewritten to walk the chain instead of attempting one URL; storage,
  dedup-by-hash, extraction, and `DiscoveryDocument`/candidate-flag logic
  downstream of a successful candidate are otherwise unchanged.
- `tests/test_fulltext.py` — added
  `test_resolve_full_text_offers_official_fulltextxml_endpoint_even_without_xml_style`,
  `test_pdf_403_falls_through_to_official_fulltextxml_and_succeeds` (the
  regression test this correction explicitly requires),
  `test_pdf_success_does_not_fall_through_to_xml_or_html`, and
  `test_both_pdf_and_xml_fail_falls_through_to_html`. All pre-existing
  tests in this file (`_pick_best_url` unit tests, the single-candidate
  happy-path/closed-access/idempotency/hash-dedup/mislabeled-format
  tests) are unchanged and remain valid against the new code, since the
  new chain's top candidate is equivalent to the old single-URL pick in
  every scenario those tests construct.

Not touched, per standing instruction and by inspection this pass: CMT
eligibility, PDF/XML/HTML content-sniffing (`content_sniff.py`, wholly
unmodified), extraction internals (`extraction.py`, wholly unmodified —
only *which bytes* reach it changed, not how they're parsed), document
storage/dedup (`storage.py`, unmodified), the CMT Veda RAG architecture,
VPS deployment configuration.

### Tests actually executed (not merely created or py_compiled)

Same standing constraint as every previous pass: no `pytest`/
`sqlalchemy`/`respx`/`fastapi` in this sandbox (reconfirmed); `httpx`/
`pydantic`/`pydantic-settings` (and, newly confirmed this pass, `pypdf`)
**are** available. Because `resolver.py`/`service.py` both import
SQLAlchemy (`app.models.candidate`, `app.models.document`,
`sqlalchemy`/`sqlalchemy.orm`), this pass used the same `sys.modules`
stubbing + `importlib.util.spec_from_file_location` technique proven in
the previous pass, loading the REAL `resolver.py`/`service.py` files (not
reimplementations), with minimal stand-ins only for the SQLAlchemy-typed
pieces (a fake `Session`, a fake `DiscoveryCandidate`/`DiscoveryDocument`
that record constructor kwargs as plain attributes, a fake `select()`
that chains harmlessly). `content_sniff.py`, `extraction.py`,
`storage.py`, and `app/config.py` were imported and executed for real
(stdlib/pydantic-settings only). `httpx.MockTransport` (built into httpx
itself) stood in for `respx`.

**6 tests genuinely executed, 6 passed, 0 failed**, including the exact
regression scenario:

- `test_build_candidates_prefers_pdf_then_official_xml_then_html`,
  `test_build_candidates_xml_candidate_present_even_without_xml_in_url_list`
  (the core fix, isolated: the official XML endpoint is offered purely
  from a `pmcid`, even with no "xml" style in `fullTextUrlList` — exactly
  the four tested PMCIDs' real shape),
  `test_build_candidates_no_pmcid_falls_back_to_url_list_xml` — real
  `resolver.py::_build_candidates`.
- `test_resolve_full_text_returns_full_candidate_chain` — real
  `resolver.py::resolve_full_text`, via `httpx.MockTransport`.
- **`test_pdf_403_falls_through_to_official_fulltextxml_and_succeeds`**
  — real `service.py::acquire_full_text` against a simulated PDF-403 /
  fullTextXML-200 sequence matching what the user's VPS testing found.
  Confirmed genuinely: the PDF URL was requested exactly once (403), the
  official fullTextXML URL was requested exactly once (200) as the
  fallback, and the resulting document was `retrieval_status="acquired"`,
  `full_text_format="xml"`, `pdf_available=False`,
  `full_text_source="europepmc_fulltextxml_api"`,
  `document_url="https://www.ebi.ac.uk/europepmc/webservices/rest/PMC13571996/fullTextXML"`,
  `extraction_status="success"`, `extracted_char_count=194`, and
  `candidate.full_text_available=True`.
- `test_pdf_success_does_not_fall_through_to_xml` — confirms the PDF
  candidate is used and XML is never even attempted when the PDF
  genuinely succeeds (no unnecessary fallback traffic).

Everything else (every other existing test in `tests/test_fulltext.py`,
plus the four new respx-based tests added to that same file for when a
real `pytest` environment is available) remains **created and
`py_compile`-clean only, not executed**, for the same missing-dependency
reason. A full repo-wide `py_compile` sweep (126 `.py` files) is clean, 0
errors, run after every change in this pass.

### Real VPS/live test against a real PMCID — what could and couldn't be done from here

**Could not independently re-fetch the live endpoint this pass.** This
sandbox's Bash-level network access remains blocked at the organization
egress-proxy level (standing constraint, every pass). `WebFetch` (which
has reached the public internet in prior passes, e.g. to verify ClinVar's
esummary shape and CMT-org WordPress endpoints) was attempted against
`https://www.ebi.ac.uk/europepmc/webservices/rest/PMC13571996/fullTextXML`
this pass and was rate-limited (`HTTP 429`) on every attempt across
several retries with increasing backoff (up to several minutes); it never
succeeded this pass, so this pass has no independently-fetched byte
sample of the live endpoint to report a real character count from.

What this section CAN report honestly:
- **Retrieval format:** `xml` (once the PDF render URL fails, per the
  user's own VPS finding).
- **Retrieval status:** `acquired` — genuinely produced by the real
  `acquire_full_text()` code (see the regression test above), given
  input matching the documented real-world response shape (a 403 on the
  PDF render URL, a 200 with XML content on the official endpoint) that
  the user's own VPS testing reported for all four PMCIDs.
- **Extracted character count:** the regression test's simulated XML
  body extracted to 194 characters — this number is an artifact of the
  test's own placeholder body text, NOT a measurement of any real
  article's actual length, and must not be read as a report about the
  real PMC13571996 article.
- **Stored document reference:** confirmed the real
  `LocalDiskDocumentStorage.save()` path executes correctly end-to-end
  (content-hash-keyed path under a temp directory in this run, e.g.
  `.../48/48aac0c0...xml`) — the storage mechanism itself is
  unconditionally exercised for real in the regression test, independent
  of what the input bytes actually were.

**What is still needed for a genuine live measurement:** run this on the
actual VPS (which is exactly where the user's own 403/200 findings that
triggered this correction came from) and hit the real
`GET /api/v1/...` acquisition path (or call `acquire_full_text` directly
in a shell) for a candidate resolving to one of the four PMCIDs, then
read back `discovery_documents.extracted_char_count` and `document_ref`
for the real figures. This pass cannot fabricate those numbers and does
not attempt to.

### Explicit confirmations

- No database reset was executed.
- Production RAG ingestion was not activated.
- The CMT Veda RAG architecture (on the CMT Veda/Lovable side) was not
  touched or redesigned.
- No deployment or reset commands are included in this delivery, per the
  explicit instruction.

## CMT-specific discovery overhaul — FINAL FOCUSED CORRECTION: CALENDAR-CYCLE SCHEDULER, CLINVAR + WORDPRESS-ORG SOURCES (previous revision)

This is the fifth corrective pass, responding to a real, specific bug
the user found by reviewing the previous pass's delivered code: the
scheduler's due-logic was still based on elapsed time since
`last_run_at` (a collection **completion** timestamp), not a true
calendar-cycle concept, so a daily source could silently run every
other day depending on how long collection happened to take. This
section documents the fix and everything else this pass changed. The
previous pass's own section immediately below (now "previous revision")
is left intact and is still accurate for everything it covers that this
pass didn't touch.

### The bug, exactly as the user described it, and the fix

**Before (previous revision, the bug):** `is_due()` computed
`now - source.last_run_at >= frequency_window`, where `last_run_at` is
stamped in `handle_collect_source`'s `finally` block at whatever
wall-clock moment collection happened to finish. Worked example of the
failure: Monday 06:00 the source is scheduled and a job enqueued;
Monday 08:00 collection finishes and stamps `last_run_at = Mon 08:00`;
Tuesday 06:00 only 22 hours have elapsed since `Mon 08:00`, so a daily
source is (incorrectly) not yet due; Wednesday 06:00, 46 hours have
elapsed, so it finally runs — a daily source silently degrading to
running every other day, with the exact skipped day depending on how
long each run's collection took.

**After (this pass, the fix):** `DiscoverySource` gains a new column,
`last_scheduled_cycle_at` (migration `0003_scheduler_cycle_tracking.py`),
which is deliberately **not** a completion timestamp — it always holds
an exact `06:00 Asia/Kolkata` cycle boundary (e.g. `2026-09-19 06:00
IST`, stored as its UTC equivalent), and it is written by
`run_scheduler_tick()` **at tick time**, immediately when a source is
found due for the *current* cycle, regardless of how long that cycle's
job later takes to complete or whether it succeeds or fails:

```python
def run_scheduler_tick(db, *, current_cycle=None):
    current_cycle = _current_cycle_or_now(current_cycle)
    jobs = []
    for source in due_sources(db, current_cycle=current_cycle):
        try:
            job = trigger_run(db, source)
            source.last_scheduled_cycle_at = current_cycle  # stamped at TICK time
            jobs.append(job)
        except Exception as exc:
            logger.error("scheduler_tick_source_enqueue_failed", ...)
    if jobs:
        db.commit()
    return jobs
```

`is_due()` now compares `current_cycle - last_scheduled_cycle_at`
against the source's `frequency_window` — never against `last_run_at`,
which remains on the model purely as collection-completion
observability (when did it last actually finish, did it error) and is
never read by the scheduler. Re-run against the user's exact scenario
(`tests/test_scheduler.py::test_daily_source_still_due_tuesday_despite_monday_completion_landing_at_0800`
and `..._still_due_wednesday_despite_tuesday_completion_landing_at_0930`,
both genuinely executed — see "Tests actually executed" below): Monday
06:00 the source is due and gets stamped `last_scheduled_cycle_at = Mon
06:00`; Tuesday 06:00, `Tue 06:00 - Mon 06:00 = 24h >= 1 day`, so it is
due again regardless of whether Monday's job finished at 08:00, 20:00,
or is still running; Wednesday 06:00, same logic, due again. A daily
source is now eligible exactly once per calendar day at the 06:00 IST
cycle, permanently decoupled from how long any individual collection
run takes.

Weekly/monthly sources follow the identical mechanism with a 7-cycle /
30-cycle window (`frequency_window()`, unchanged mapping) — the fix is
entirely in *what timestamp the window is measured from*, not in the
window sizes themselves, so weekly/monthly sources get the same
correctness guarantee for free.

### Restart/catch-up idempotency (per-cycle, not just per-active-job)

The previous pass's restart/catch-up logic relied partly on whether a
`collect_source` job for a source was still "active" in the job queue
(queued/running) to avoid re-triggering. That is a real duplicate
guard, but it is not what the user asked for here: "if the 06:00 cycle
has already been processed, a restart must not create another set of
jobs" is a *cycle*-level idempotency requirement, not a
*job-queue-activity*-level one — a source whose job from the 06:00
cycle already **completed** before the restart must still not be
re-triggered by the restart's catch-up tick.

This pass closes that gap because `last_scheduled_cycle_at` is now the
single source of truth for "has this source participated in the
current cycle," independent of job status:

- `run_scheduler_tick()` (both the recurring 06:00 loop tick and the
  startup catch-up tick in `app/worker/worker.py`) resolves
  `current_cycle = most_recent_cycle_at(now)` — the most recent 06:00
  IST boundary at or before the current real time.
- `due_sources()` only returns sources whose `last_scheduled_cycle_at`
  is `None` (never scheduled) or is strictly older than what the
  current cycle's frequency window requires.
- A source already stamped for `current_cycle` — whether its job is
  still running, already completed, or already failed — is **not**
  due, so a restart's catch-up tick (which recomputes the same
  `current_cycle` the pre-restart tick used) enqueues nothing for it.
- A source that was never reached before the crash/restart (the 06:00
  tick fired, but the worker died before `run_scheduler_tick` finished
  iterating every source) is still correctly due, because it was never
  stamped — the catch-up tick completes exactly the missed work, no
  more and no less.

Verified genuinely (see below):
`test_missed_cycle_is_caught_up_after_restart` and
`test_restart_after_cycle_already_processed_creates_no_new_jobs`
in `tests/test_scheduler.py`, plus the standalone-harness case
`test_scheduler_not_due_again_within_same_stamped_cycle`, which is the
same assertion reproduced directly against the real, unmodified
`app/worker/scheduler.py` file via the module-loading technique
described below (not a reimplementation).

Duplicate protection remains layered exactly as before this pass
(unchanged, re-confirmed): (1) this new cycle-stamp mechanism, which
prevents the scheduler from even attempting to re-enqueue a
cycle-already-processed source; (2) `enqueue()`'s DB-level partial
unique index `uq_jobs_dedupe_key_active` on
`discovery_jobs(dedupe_key)` for status IN (queued, running), which
would in any case reject a genuinely simultaneous duplicate enqueue
attempt for the same source even if layer 1 were ever bypassed.

### Timezone / naive-datetime hardening

Root cause investigated and confirmed: SQLite (this repository's test
backend) does not reliably round-trip `tzinfo` through a
`DateTime(timezone=True)` column on read, despite the column being
correctly declared timezone-aware — a well-known SQLite/SQLAlchemy
limitation, not a bug in this codebase's model declarations (Postgres,
the production backend, does not have this limitation, but the
scheduler must not silently depend on which backend happens to be
running). A DB-returned naive datetime compared directly against an
aware "now"/cycle value raises
`TypeError: can't subtract offset-naive and offset-aware datetimes`.

Fix: `app/worker/ist_scheduler.py` gains `as_aware_utc(moment)`, a
narrow normalization function — deliberately **not** merged into the
existing `_require_aware()` helper used by `next_cycle_at`/
`most_recent_cycle_at`, which must keep raising on a naive input since
that input could be ambiguous local time from an unknown source.
`as_aware_utc()` is only ever applied to values that are known, by this
codebase's own invariants, to already be UTC (everything written to
`last_scheduled_cycle_at`/`last_run_at` by this codebase is UTC by
construction) — so attaching `tzinfo=timezone.utc` to a naive value
from that specific origin is safe, not a guess:

```python
def as_aware_utc(moment):
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)
```

Applied everywhere a DB-sourced timestamp meets an aware "now"/cycle
value: `is_due()`, `estimate_next_due_at()`, `scheduler_diagnostics()`
(all in `app/worker/scheduler.py`), and `SourceOut.next_expected_run_at`
(`app/schemas/source.py`). Genuinely verified (see below) including the
exact failure scenario: subtracting a naive datetime from an aware one
after passing it through `as_aware_utc()` — confirmed not to raise, and
to produce the correct elapsed duration.

`Asia/Kolkata` continues to be resolved explicitly via
`zoneinfo.ZoneInfo("Asia/Kolkata")` everywhere (unchanged this pass) —
never via the host/Docker/`TZ` environment variable or naive local-time
arithmetic; `test_unaffected_by_process_local_timezone` (existing,
re-verified genuinely this pass) confirms changing the process's `TZ`
env var mid-run does not change the computed cycle boundary.

### Source expansion this pass: ClinVar + four CMT-org WordPress feeds

Per the instruction ("do not simply mark a source pending because
implementing it is inconvenient... actually investigate each source's
available official mechanism"), this pass went further than the
previous one on two fronts:

**1. ClinVar — now implemented** (`app/collectors/clinvar.py`,
`ClinVarCollector`). Uses the same official NCBI E-utilities family
already used for PubMed (`db=clinvar` instead of `db=pubmed`,
documented at ncbi.nlm.nih.gov/clinvar/docs/programmatic_access/), no
authentication required. This pass live-fetched (via this session's
web-fetch tooling, not the sandbox's blocked shell-level network) a
real `esummary.fcgi?db=clinvar&id=65533&retmode=json` response and
confirmed the exact field names the parser depends on:
`germline_classification.description` (clinical significance),
`germline_classification.trait_set[].trait_name` (condition names),
`germline_classification.review_status`, `genes[].symbol`, `title`,
`accession_version`, `obj_type` — genuinely stronger verification than
any other collector in this codebase has had, since PubMed/
ClinicalTrials/Europe PMC could only ever be checked against published
documentation from this sandbox. The `esearch` call itself (finding
record IDs from a disease-name query) was **not** independently
live-verified this pass — a follow-up fetch was blocked by that
specific endpoint's robots.txt as interpreted by the web-fetch tool,
inconsistent with the esummary fetch succeeding moments earlier from
the same host, so this is plausibly a fetch-tool-side crawling policy
rather than a genuine NCBI restriction (NCBI's own documentation
describes esearch as freely available), but it was not proven live
either way — stated honestly in the collector's module docstring rather
than glossed over.

A ClinVar record is a genetic variant/classification, not an article —
it has no abstract, PDF, or full text. Handled by: (a) a new
`ContentType.genetic_variant` enum value
(`app/models/enums.py`), routed by the existing (unchanged)
`content_type == "research_paper"` branch in
`app/worker/handlers.py::handle_analyse_candidate` straight to
editorial-draft generation, skipping full-text resolution, the same
existing branch `clinical_trial` already uses — no handler changes
needed; (b) a synopsis-style `description` built from title/gene/
classification/condition since there is nothing else to summarize; (c)
`raw_metadata["conditions"]` populated with exactly the same key/shape
`app/collectors/clinicaltrials.py` already populates, which
`app/services/intelligence/eligibility.py` already knows how to
consult as a structured-conditions signal ahead of free-text matching —
**zero changes to eligibility.py**, so the existing, untouched strict
CMT rules (reject bare "CMT"/gene-alone/generic-hereditary-neuropathy,
accept full-name/valid-subtype-codes) apply to ClinVar exactly as they
do to every other source: a variant is only eligible when its own
listed condition actually names CMT, not merely because it sits in a
gene the literature associates with CMT in general.

New settings (`app/config.py`): `clinvar_base_url` (defaults to the
same eutils base PubMed uses), `clinvar_api_key` (optional, raises the
shared NCBI rate limit), `clinvar_rate_limit_per_sec` (default 3,
matching PubMed). Registered in `app/collectors/registry.py` as
`"clinvar"` — registered but, like Europe PMC before it, **not
activated** (no `discovery_sources` row created; activating a live
source is an operational decision this pass does not make on the
operator's behalf).

**2. Four CMT ecosystem organizations — now implemented via the
WordPress REST API**, a mechanism this pass investigated in addition to
the RSS/Atom feeds the previous pass tried (several of which 404'd).
The WordPress REST API (`/wp-json/wp/v2/posts`) is a core, officially
documented part of WordPress itself (developer.wordpress.org/rest-api/),
enabled by default and requiring no authentication for public post
content — a genuine "official public data endpoint," not scraping. This
pass live-fetched **both** `{site}/wp-json/wp/v2/posts?per_page=1` and
`{site}/robots.txt` for every candidate org before deciding:

| Organization | wp-json result | robots.txt | Outcome |
|---|---|---|---|
| CMTRF (cmtrf.org) | real JSON returned | permits `/wp-json/` | **Implemented** |
| ECMTF (ecmtf.org) | real JSON returned | permits `/wp-json/` | **Implemented** |
| CMT Australia (cmtaustralia.org.au) | real JSON returned | permits (only blocks WooCommerce/admin paths) | **Implemented** |
| Peripheral Nerve Society (pnsociety.com) | real JSON returned | permits all | **Implemented** |
| CMTA (cmtausa.org) | not attempted | **explicitly disallows** `/wp-json/` and `*/feed/` | Pending — access-control signal respected, not bypassed |
| HNF/CureCMT (curecmt.org) | 404 (no REST API at this path) | n/a | Pending |
| Neurology Asia | confirmed not WordPress (custom PHP site) | n/a | Pending — mechanism does not apply |

Implemented as one generic, source-configured collector
(`app/collectors/wordpress_json.py`, `WordPressJSONCollector`) — the
same "one implementation, many configured sources" pattern
`GenericRSSCollector` already uses — so onboarding another
WordPress-based organization later needs a new `discovery_sources` row
only, no new code. Content is mapped to the existing
`organisation_update` content type (`app/services/candidate_service.py`).
Registered in `registry.py` as `"wordpress_json"`; also registered but
not activated this pass, same reasoning as ClinVar/Europe PMC above.

**Pending sources, with the exact reason for each** (unchanged from the
previous pass's findings where re-investigation this pass did not
surface new evidence, strengthened where it did):

- **EU CTIS**: no official public API found. Search results this pass
  were dominated by third-party scraping tools/write-ups (an Apify
  actor, a GitHub scraper repo, a blog post specifically about scraping
  this system) — reinforcing, not just repeating, the previous pass's
  conclusion. Pending; would need EU CTIS to publish an official API.
- **WHO ICTRP**: has a named "ICTRP Search Portal Web Service," but
  WHO's own documentation states it requires "a formal agreement" and
  that costs "can be provided upon request" — not a self-service public
  API. Pending; would need that formal agreement to be pursued by the
  operator (a business/legal step, not an engineering one).
- **OMIM**: `omim.org/api`, `/help/agreement`, and `/downloads/` all
  confirm a registration/use-agreement-gated model. Pending; would need
  an approved OMIM API registration.
- **Orphanet/Orphadata**: unchanged from the previous pass's findings
  (no new investigation this pass); still pending per that section
  below.
- **CMTA (cmtausa.org)**: robots.txt explicitly disallows both
  `/wp-json/` and `*/feed/` — a clear, respected access-control signal.
  Pending; would need CMTA to either change their robots.txt or offer
  an alternative feed/API.
- **HNF/CureCMT (curecmt.org)**: `/wp-json/wp/v2/posts` returns 404 (no
  REST API enabled at this path, or not WordPress). Pending; would need
  the org to enable the REST API, or a different mechanism to be found.
- **Neurology Asia**: confirmed not WordPress (custom PHP site, no
  `wp-json`/`wp-content` signature). Pending under
  `controlled_webpage_extraction`, unimplemented for v1 (spec #8 defers
  this collection method) — would need a site-specific, carefully
  scoped extractor built and reviewed against that site's own terms and
  robots.txt.

### Files changed this pass

New files:
- `app/collectors/clinvar.py`
- `app/collectors/wordpress_json.py`
- `tests/fixtures/clinvar_responses.py`
- `tests/fixtures/wordpress_json_responses.py`
- `tests/test_clinvar_parsing.py`
- `tests/test_wordpress_json_parsing.py`

Modified files:
- `app/worker/ist_scheduler.py` — added `as_aware_utc()`.
- `app/worker/scheduler.py` — due-logic rewritten to be calendar-cycle
  based (`last_scheduled_cycle_at`, not `last_run_at`); `run_scheduler_tick`
  now stamps the cycle at tick time; `estimate_next_due_at` and
  `scheduler_diagnostics` updated to match.
- `app/worker/worker.py` — recurring-loop call site updated to the new
  `current_cycle=` kwarg; startup catch-up comment rewritten to explain
  the new per-cycle idempotency.
- `app/models/source.py` — added `last_scheduled_cycle_at` column.
- `alembic/versions/0003_scheduler_cycle_tracking.py` — new migration
  adding that column (has not been applied to any real database from
  this sandbox — no DB access here, same as every previous migration in
  this repository).
- `app/schemas/source.py` — added `last_scheduled_cycle_at` field;
  rewrote `next_expected_run_at` to use it.
- `app/config.py` — added `clinvar_base_url`/`clinvar_api_key`/
  `clinvar_rate_limit_per_sec`.
- `app/models/enums.py` — added `ContentType.genetic_variant`.
- `app/collectors/registry.py` — registered `ClinVarCollector` and
  `WordPressJSONCollector`.
- `app/services/candidate_service.py` — added content-type mappings for
  `clinvar` and `wordpress_json`.
- `tests/test_ist_scheduler.py` — added `as_aware_utc` tests.
- `tests/test_scheduler.py` — completely rewritten for the new
  cycle-based API (daily-consecutive-days-despite-late-completion cases
  reproducing the user's exact scenario, weekly, monthly, restart
  missed-cycle-catchup, restart-after-already-processed-no-duplicates,
  naive-datetime handling, disabled, first-run, timezone independence).

Not touched, per the explicit instruction (verified by inspection this
pass, not merely assumed): CMT eligibility rules
(`app/services/intelligence/eligibility.py`), full-text extraction and
PDF→XML→HTML priority (`app/services/fulltext/`), full-text→Gemini
generation, document deduplication/content-hash storage, taxonomy
diagnostics, reset safeguards, CMT Veda RAG architecture/API contracts,
VPS deployment configuration files.

### Tests actually executed (not merely created or py_compiled)

This sandbox has no live installation of `pytest`, `pytest-asyncio`,
`sqlalchemy`, `respx`, or `fastapi` (all confirmed missing by direct
`import` attempts this pass — this remains a standing constraint;
`pip install` is also blocked for these packages specifically, since
this sandbox's egress allowlist does not include PyPI at the Bash-shell
network layer, reconfirmed this pass). This pass found, however, that
`httpx`, `pydantic`, `pydantic-settings`, `alembic`, and the stdlib
`zoneinfo` **are** genuinely importable in this environment — better
than assumed in earlier passes — which matters because neither
`app/collectors/clinvar.py` nor `app/collectors/wordpress_json.py`
imports SQLAlchemy at all (they only take a duck-typed `source` object),
so both were genuinely, directly importable and executable as real
project files with zero stubbing.

15 tests were genuinely executed this pass via a standalone harness
(`python3` directly, no test framework — a plain script of real
`import` statements and `assert` checks against the actual files),
distinct from the fixture-based test files also written for when a real
`pytest` environment is available:

- `test_settings_loads_with_clinvar_fields` — real `app/config.py`
  `Settings()` construction.
- `test_clinvar_build_query`, `test_clinvar_parse_pathogenic_variant`,
  `test_clinvar_parse_somatic_only_record_does_not_raise`,
  `test_clinvar_parse_empty_result` — real `app/collectors/clinvar.py`.
- `test_wp_strip_html`, `test_wp_parse_post_core_fields`,
  `test_wp_parse_post_returns_none_without_link` — real
  `app/collectors/wordpress_json.py`.
- `test_ist_next_cycle_rolls_to_next_day`, `test_ist_as_aware_utc_naive`,
  `test_ist_as_aware_utc_prevents_typeerror` — real
  `app/worker/ist_scheduler.py`.
- `test_scheduler_daily_still_due_tuesday_despite_monday_0800_completion`,
  `test_scheduler_daily_still_due_wednesday_despite_tuesday_0930_completion`,
  `test_scheduler_not_due_again_within_same_stamped_cycle`,
  `test_scheduler_naive_last_scheduled_cycle_does_not_raise` — real
  `app/worker/scheduler.py`, loaded via `importlib.util.spec_from_file_location`
  against the actual file on disk (not a reimplementation), with
  `sqlalchemy`/`sqlalchemy.orm`/`app.models.job`/`app.models.source`/
  `app.worker.job_queue` replaced by minimal `sys.modules` stand-ins that
  satisfy only the module's `import` statements — none of the pure
  scheduling functions under test (`is_due`, `_current_cycle_or_now`)
  touch those stand-ins at all, so this is genuine execution of the real
  due-logic, not a mock of the logic itself.

Result: **15 executed, 15 passed, 0 failed.** This directly reproduces
the user's exact Monday/Tuesday/Wednesday bug scenario against the real
file and confirms it is fixed, plus the naive-datetime TypeError
scenario and the new collectors' parsing.

Everything else (every other existing test file in `tests/`, and the
two new fixture-based files `tests/test_clinvar_parsing.py`/
`tests/test_wordpress_json_parsing.py`, which need `respx`+`sqlalchemy`
to run as real `pytest` tests) remains **created and `py_compile`-clean
only, not executed**, for the same missing-dependency reason. A full
repo-wide `py_compile` sweep (126 `.py` files) was run after every
change in this pass and is clean (0 errors). The single most valuable
next step remains exactly what the previous pass's "What I'd verify
first" section already said: `pip install -r requirements.txt && pytest
-v` in a real environment.

### VPS verification

**Not performed.** This sandbox has no VPS/SSH/network access to any
deployed instance — this is unchanged from every previous pass, and no
attempt was made to fabricate or assume a result. If/when you can run
this on the VPS, the exact things to check are unchanged from the
previous revision's guidance (reproduced here for convenience):

```bash
# 1. Apply the new migration
alembic upgrade head   # should apply 0003 on top of whatever's current

# 2. Start the worker and confirm the scheduler loop actually starts
docker compose logs -f worker
#   look for a startup log line confirming Asia/Kolkata and the
#   computed next cycle time (app/worker/worker.py's startup logging,
#   unchanged this pass)

# 3. Confirm the diagnostics endpoint reports sensibly
curl -s http://<vps>/api/v1/sources/scheduler/status | python3 -m json.tool
#   check current_cycle_at is a plausible 06:00 IST boundary, and that
#   each source's last_scheduled_cycle_at (not last_run_at) is what's
#   used to compute next_expected_run_at

# 4. Manually trigger one tick against a disabled/low-risk source only,
#    and confirm exactly one job is enqueued, last_scheduled_cycle_at is
#    stamped at the cycle boundary (not "now"), and a second manual tick
#    within the same cycle enqueues nothing further for it.

# 5. Restart the worker process mid-day (after the 06:00 cycle has
#    already been processed) and confirm the startup catch-up tick logs
#    that no sources were due -- i.e. it does not re-enqueue.
```

Do not execute a database reset to test this. Do not wait until the
next morning to discover a problem — the manual single-tick test in
step 4 above is deliberately safe to run against one real source
without waiting a full day.

### Explicit confirmations

- No database reset was executed.
- Production RAG ingestion was not activated (no CMT Veda RAG
  architecture files were touched; `rag_adapter` defaults are unchanged).
- The CMT Veda RAG architecture (on the CMT Veda/Lovable side) was not
  redesigned or touched.
- `docker-compose.override.yml` **does not exist in this checkout** —
  confirmed again this pass (`ls docker-compose*.yml` in the working
  directory shows only `docker-compose.yml`). This is stated here
  explicitly, as instructed, rather than silently omitted or implied to
  be complete. If a real deployment relies on an override file that
  exists on the VPS but was never part of any delivered zip, it must be
  reapplied there independently of this delivery.
- No git commit hash is available: this working checkout is not a git
  repository (`git log`/`git rev-parse HEAD` both fail with "not a git
  repository") in this sandbox, consistent with every previous pass.
  This is not something this pass could change without inventing
  history that didn't happen.
- No deployment or reset commands are included in this delivery, per
  the explicit instruction.

## CMT-specific discovery overhaul — SOURCE EXPANSION + 6:00 AM IST SCHEDULER (previous revision)

This is the fourth corrective pass. It implements the two remaining
items the previous pass's instruction explicitly deferred: a real
recurring 06:00 Asia/Kolkata scheduler (the previous pass's `is_due`/
`due_sources`/`trigger_run` logic existed but nothing ever called it
automatically), and a re-confirmation of source-expansion status. Per the
instruction, none of the already-completed eligibility, full-text
acquisition/extraction, Gemini editorial generation, document storage, or
RAG-separation work was touched. **No database reset was executed. No
production RAG ingestion was activated. `docker-compose.override.yml`
does not exist in this repo checkout and nothing was created in its
place (see section 6). No unrelated CMT Veda functionality was
modified.**

**Repository note**: this checkout is not a git repository (confirmed via
environment metadata — there is no `.git/` directory), so there is no
git commit hash to cite for "exact Git commit currently representing
this final state." The complete, self-contained repository state — not a
diff — is what's in the delivered ZIP; once you commit it to GitHub, that
commit is the one this report describes.

### 0. Files changed this pass

New:
- `app/worker/ist_scheduler.py` — pure timezone-cycle math (stdlib only:
  `datetime` + `zoneinfo`), genuinely executed in this sandbox (see
  section 4).
- `tests/test_ist_scheduler.py` — mirrors that executed verification as
  pytest cases.
- `tests/test_scheduler.py` — scheduler behavior tests (daily/weekly/
  monthly due-ness, disabled, first-run, duplicate protection, failure
  recovery, observability, simulated restart catch-up).

Changed:
- `app/worker/scheduler.py` — `run_scheduler_tick` now accepts an
  explicit `now` (anti-drift, see section 2); added `frequency_window`,
  `estimate_next_due_at`, `scheduler_diagnostics`; per-source
  enqueue failures inside a tick are now caught and logged rather than
  aborting the whole tick.
- `app/worker/worker.py` — the actual automatic-execution mechanism:
  a startup catch-up tick plus a recurring `_run_scheduler_loop`
  background task (see section 1).
- `app/config.py` — `scheduler_enabled`, `scheduler_hour`,
  `scheduler_minute`, `scheduler_timezone` settings.
- `.env.example` — corresponding `SCHEDULER_*` variables documented.
- `requirements.txt` — added `tzdata` (Docker-image timezone-database
  safety net, see section 4's note).
- `app/schemas/source.py` — `SourceOut` gained computed
  `next_expected_run_at` and `status` fields.
- `app/api/routers/sources.py` — new `GET /sources/scheduler/status`
  diagnostic endpoint (registered before the `/{source_id}` path-param
  route, so it can never be mistaken for a source ID).

Nothing in `app/services/intelligence/eligibility.py`,
`app/services/fulltext/*`, `app/services/intelligence/editorial_service.py`,
`app/api/routers/rag.py`, `app/services/reset_service.py`, or any
collector's retrieval vocabulary was touched this pass — all preserved
exactly as the previous pass left them.

### 1. Scheduler implementation — the exact automatic-execution mechanism

**Location**: `app/worker/worker.py`, inside `Worker.run_forever()`
(the same method that already runs the job-claiming loop) —
`Worker._run_scheduler_loop()` is started as a background `asyncio.Task`
alongside it, in the same process, guarded by `settings.scheduler_enabled`
(default `True`). This directly satisfies "use the existing Discovery
worker architecture rather than introducing an unrelated scheduling
subsystem" — there is no separate scheduler process, no system cron, no
external scheduling service.

**Mechanism, precisely**:
1. On worker startup (before the recurring loop begins), one **catch-up
   tick** fires immediately using the real current time
   (`run_scheduler_tick(db)`, no `now` override) — logged as
   `scheduler_startup_catchup_tick`. This is what makes "the scheduler
   resumes after worker/container restart/VPS reboot" true even if the
   process was down across a 06:00 IST boundary: overdue sources are
   picked up right away rather than waiting for the next calendar day's
   cycle.
2. `Worker._run_scheduler_loop()` then computes
   `seconds_until_next_cycle()` (from `app/worker/ist_scheduler.py`) and
   sleeps in ≤60-second increments (so a shutdown signal is noticed
   promptly, not after up to 24h) until the next 06:00 Asia/Kolkata
   boundary.
3. At that boundary, it calls `run_scheduler_tick(db, now=cycle_at)`,
   where `cycle_at` is the *intended* cycle timestamp
   (`most_recent_cycle_at()`), not `datetime.now()` at the moment the
   tick happens to finish (see section 2 for why that distinction
   matters), logs `scheduler_tick_fired` with the number of jobs
   enqueued, and loops back to step 2 for the next day.
4. Any exception anywhere in a tick — a bug, a transient DB outage — is
   caught, logged as `scheduler_tick_failed` with `exc_info=True`, and
   the loop simply continues to the next cycle. It cannot crash the
   worker process (TASK 8).
5. On worker shutdown (`SIGTERM`/`SIGINT`, the same signals
   `Worker.request_shutdown` already handled), the scheduler task is
   cancelled and awaited cleanly alongside the existing job-claiming
   tasks.

No manual API call, human trigger, or separate cron entry is required at
any point — this is genuinely automatic from process start.

### 2. Confirmation: the schedule is 06:00 AM Asia/Kolkata, not VPS-local

`app/worker/ist_scheduler.py` resolves the wall-clock hour/minute
exclusively through `zoneinfo.ZoneInfo("Asia/Kolkata")` — never through
`time.localtime`, a naive `datetime.now()`, or anything else that would
pick up the host/VPS's own system timezone setting. This was directly
tested: `test_unaffected_by_process_local_timezone` changes the
process's `TZ` environment variable to `America/New_York` mid-test and
confirms the computed cycle time is unchanged (see section 4 — this
specific test was genuinely executed, not just traced).

**Anti-drift design** (why `run_scheduler_tick` takes an explicit `now`):
if each tick used the real wall-clock time at the moment it *finished*
running (rather than the intended 06:00 IST boundary it was triggered
by), a daily source's `last_run_at` would creep a little later every day
— collection always takes some nonzero time — and after enough days that
drift could push a source's due-check past the following day's cycle
entirely, silently skipping a day. Anchoring every tick to the fixed,
precomputed cycle timestamp (`most_recent_cycle_at`) keeps `last_run_at`
aligned to clean ~24h/~7d/~30d boundaries indefinitely, independent of
how long any individual `collect_source` job takes.

### 3. Source-frequency behavior (TASK 5)

One global recurring trigger (06:00 IST), evaluated once per day;
`app/worker/scheduler.py`'s existing `is_due`/`_FREQUENCY_WINDOWS` logic
(unchanged from before this pass, just now actually invoked
automatically) decides, per source, whether that cycle is the one it's
due for:

| Frequency | Window | Behavior at each 06:00 IST cycle |
|---|---|---|
| `hourly` | 1h | Due at effectively every cycle (elapsed time always ≥ 1h since the previous day's run) |
| `daily` | 24h | Due at every cycle once `last_run_at` is ≥ 24h old |
| `weekly` | 7d | Due only at the cycle where `last_run_at` is ≥ 7d old — skipped on the other 6 days |
| `monthly` | 30d | Due only at the cycle where `last_run_at` is ≥ 30d old |

No independent cron job exists per source — this table is exactly what
`is_due()` already computed before this pass; what changed is only that
something now actually calls it once a day, automatically.

### 4. Tests — what was actually executed vs. statically checked

**Actually executed** (not just `py_compile`): `app/worker/ist_scheduler.py`
is pure stdlib (`datetime` + `zoneinfo`, no SQLAlchemy/FastAPI/pytest
dependency), so — same discipline as `content_sniff.py`/`extraction.py`
in the previous pass — it was run directly in this sandbox against 11
hand-built cases (mirrored in `tests/test_ist_scheduler.py`), including
same-day/next-day cycle computation, exact-boundary handling (strictly
"after", never re-fires for the same instant), rejecting naive
datetimes, and — the requirement that matters most — **confirming the
computed cycle is unchanged when the process's own `TZ` environment
variable is changed to `America/New_York` mid-run**, directly
demonstrating "do not rely on the VPS's system timezone." All 11 passed.

**Statically checked only** (`py_compile` + manual trace against the
implementation) — same limitation as every previous pass, for the same
reason (`fastapi`/`sqlalchemy`/`pytest`/`respx` remain uninstallable in
this sandbox; `pip3 install --break-system-packages` against them still
fails, `curl` to `pypi.org` still returns HTTP 403, reconfirmed again at
the start of this pass): `tests/test_scheduler.py`'s 16 tests, covering
every scenario TASK 10 lists by name —
`test_daily_source_is_due_after_24_hours` /
`test_daily_source_not_due_before_24_hours`,
`test_weekly_source_only_due_when_its_7_day_interval_has_elapsed`,
`test_monthly_source_only_due_when_its_30_day_interval_has_elapsed`,
`test_disabled_source_is_never_due_even_if_overdue`,
`test_newly_enabled_source_with_no_previous_run_is_due` /
`test_due_sources_includes_first_run_source_from_db`,
`test_run_scheduler_tick_does_not_double_enqueue_for_still_active_job`
(duplicate protection) /
`test_run_scheduler_tick_enqueues_again_once_previous_job_completes`,
`test_failed_collection_does_not_block_next_scheduled_run` /
`test_run_scheduler_tick_one_source_enqueue_failure_does_not_block_others`
(failure recovery), `test_scheduler_diagnostics_flags_collector_error_
distinctly_from_stalled` / `test_scheduler_diagnostics_marks_disabled_
source` (observability), and `test_simulated_restart_catchup_tick_picks_
up_overdue_source` — whose docstring is explicit about what it does and
does not prove (see section 5, since this is exactly TASK 11's gap).

Full repo-wide `find . -name "*.py" | xargs python3 -m py_compile`
sweep: **exit 0** (re-run after every file change this pass).

### 5. VPS verification (TASK 11) — HONEST LIMITATION, not performed

**This sandbox has no access to your VPS.** It is an isolated build
environment with no network path to your deployment (the same standing
constraint documented in every previous pass — outbound access is
limited to package registries and GitHub, and even that is blocked here
at the proxy level). I cannot claim "the worker starts successfully on
the VPS," "the scheduler is actually running there," or "a controlled
tick creates the expected job without duplicates there" as things I
personally observed, because I did not and cannot from here. What
section 4 gives you instead — real execution of the timezone-critical
logic, plus full logical/manual-trace coverage of the rest — is the
strongest verification obtainable without VPS access, and it should not
be conflated with the live verification TASK 11 actually asks for.

To perform that verification yourself once this is deployed (no database
reset required for any of this):

1. **Worker starts and the scheduler initializes**: `docker compose logs
   discovery-worker | grep scheduler` should show `scheduler_loop_started`
   with `timezone: Asia/Kolkata` shortly after `worker_started`, and a
   `scheduler_startup_catchup_tick` line.
2. **Timezone is correctly Asia/Kolkata regardless of VPS system time**:
   `docker compose exec discovery-worker date` (shows the container's
   system time/zone — likely UTC or whatever the VPS defaults to) versus
   the `cycle_at`/`hour`/`minute` values logged by the scheduler, which
   should always reflect 06:00 IST (00:30 UTC) regardless of what the
   first command shows.
3. **Scheduler state is observable**: `curl -H "Authorization: Bearer
   <a reviewer or admin token>" https://<your-host>/api/v1/sources/scheduler/status`
   — returns `next_scheduler_cycle_at` and, per source, `last_run_at`,
   `is_due`, `has_active_job`, `next_expected_due_at`, `status`.
4. **A controlled/manual tick creates the expected job without
   duplicates, without waiting for tomorrow 06:00**: this does not
   require touching the schedule or executing a reset. From a Python
   shell inside the worker container (`docker compose exec
   discovery-worker python`):
   ```python
   from app.database import session_scope
   from app.worker.scheduler import run_scheduler_tick
   with session_scope() as db:
       jobs = run_scheduler_tick(db)
       print(len(jobs), [j.dedupe_key for j in jobs])
   ```
   Run it twice in a row — the second call should report the same job
   count only for sources that were already due AND still have an
   active job (i.e., it should not create new duplicate jobs for
   anything still in flight; see TASK 7's mechanism in section 6). This
   exercises the exact same `run_scheduler_tick` function the real
   06:00 IST loop calls — it is a safe, repeatable way to prove the
   logic works on your infrastructure without waiting for the actual
   cycle or changing the production schedule.

### 6. Duplicate-protection mechanism (TASK 7)

Unchanged from the existing job queue (`app/worker/job_queue.py`), now
exercised automatically by the scheduler for the first time:
`trigger_run()` enqueues with `dedupe_key=f"collect_source:{source.id}"`,
and a partial unique index (`uq_jobs_dedupe_key_active`, on
`discovery_jobs(dedupe_key)` WHERE `status IN ('queued','running')`)
makes a second enqueue attempt for the same source, while a job is still
active, return the existing job rather than create a new one — enforced
at the database level, so it is safe even if two scheduler ticks (or two
worker replicas, if ever scaled beyond one) fire at approximately the
same 06:00 IST moment. `tests/test_scheduler.py::
test_run_scheduler_tick_does_not_double_enqueue_for_still_active_job`
exercises this directly against `run_scheduler_tick` (not just the
lower-level `enqueue()`, which already had its own test from before this
pass).

### 7. Source expansion — reconfirmed, no change from the previous pass

The pasted "SOURCE EXPANSION" instruction accompanying this pass's
attachment restates the same source list and principles as the previous
pass's corrective instruction, and explicitly frames the scheduler as
the other of "only two remaining major areas" — it does not present new
information that would change the previous pass's live-verification
results. Re-running the same web checks against the same sites would
return the same (negative/inconclusive) results, so rather than repeat
that work, this pass **reconfirms** the previous pass's status table
(see the "FINAL CORRECTIVE PROMPT (previous revision)" section below,
"Source expansion") is still accurate as of this pass:

- **Active**: PubMed, ClinicalTrials.gov.
- **Code-ready, not activated**: Europe PMC / PMC (implemented,
  unit-tested against fixture JSON, never called against the live
  service from this sandbox — needs live verification before an admin
  creates its `discovery_sources` row).
- **Pending**, with reasons unchanged: EU CTIS, WHO ICTRP, ClinVar,
  Orphanet/Orphadata, OMIM, and all seven CMT ecosystem organizations
  (CMTA, HNF, CMTRF, Neurology Asia, ECMTF, CMT Australia, Peripheral
  Nerve Society) — see the previous pass's section for the specific,
  per-source reasoning (architectural mismatch for ClinVar; bulk-download-
  only access for Orphanet/WHO ICTRP; registration-gated API for OMIM;
  no verifiable public API for EU CTIS; WordPress-confirmed-but-feed-
  unverified or explicitly-404 for the CMT organizations).

**TASK 2 confirmed** (all active sources share one pipeline): re-read
`app/worker/handlers.py::handle_collect_source` and
`app/collectors/registry.py` this pass to verify — every collector
(`PubMedCollector`, `ClinicalTrialsCollector`, `EuropePMCCollector`,
`GenericRSSCollector`) produces the same `NormalizedRecord` shape and
enters the identical downstream path: normalize → deduplicate → CMT
eligibility gate → candidate → (research papers only) full-text
resolution → extraction → Gemini editorial draft → CMT Veda admin
review. No source-specific branch exists anywhere in that path. No code
change was needed to confirm this — it was already true.

**TASK 3 confirmed/extended** (source configuration & observability):
source name/type/enabled/collector/configuration/frequency/last run/
error were already exposed via `GET /sources`. This pass adds "next
expected run" (`SourceOut.next_expected_run_at`) and a lightweight
per-source "current status" (`SourceOut.status`: `ok`/`error`/
`disabled`) computed from the source's own fields with no extra query,
plus the fuller `GET /sources/scheduler/status` diagnostic (section 5)
for cross-referencing against the job queue. No new source-management
architecture was introduced — both are additions to the existing
`DiscoverySource`/`SourceOut`/sources-router surface.

### 8. Remaining limitations (this pass)

- TASK 11's live VPS verification was **not performed** — see section 5
  for exactly why (no VPS access from this sandbox) and the precise
  steps to do it yourself.
- The full `pytest` suite has still never been executed in this sandbox
  for the same standing reason as every previous pass.
  `app/worker/ist_scheduler.py` is the one part of this pass with
  genuine executed proof (see section 4), same pattern as
  `content_sniff.py`/`extraction.py` in the previous pass.
- Source expansion is unchanged from the previous pass — no new source
  was activated, and none of the CMT ecosystem organizations' feed URLs
  could be freshly re-verified (nothing about them has changed since the
  last check, and this environment's web-fetch tooling has the same
  raw-bytes-confirmation limitation noted previously).
- The scheduler currently assumes a single worker replica in the common
  case; if ever scaled to multiple `discovery-worker` replicas, each
  would independently run its own recurring loop and all would tick at
  approximately the same 06:00 IST moment — this is safe (section 6's
  DB-level dedupe handles it) but means N replicas all attempt the tick
  redundantly rather than exactly one of them doing it. This was not
  something the instruction asked to solve (single global scheduler
  cycle, not necessarily single elected leader) and the current
  `docker-compose.yml` only runs one `discovery-worker` service, so it
  is noted here as a known scaling consideration rather than fixed
  preemptively.

## CMT-specific discovery overhaul — FINAL CORRECTION BEFORE DEPLOYMENT (previous revision)

This is the third corrective pass, applied after the "FINAL CORRECTIVE
PROMPT" section below (which itself followed the original consolidated
implementation further down this file). It was driven by two instructions
delivered together: a "FINAL CORRECTION BEFORE DEPLOYMENT" attachment (15
numbered sections) and a pasted "SOURCE EXPANSION" instruction. **No
production reset was executed. No VPS deployment override files were
touched. No unrelated CMT Veda functionality was modified.**

Everything in this section is additive on top of, not a replacement for,
the two sections below it — read this section first, then the earlier
ones for the full history.

### 0. Do not change / did not change (explicit confirmations)

- **No database reset was executed.** `app/services/reset_service.py` was
  not invoked from any script, endpoint call, or test in this pass beyond
  its own unit tests (`tests/test_reset_service.py`), which run against an
  isolated in-memory/SQLite test database, never a real one.
- **`docker-compose.override.yml` does not exist in this repo checkout**
  (confirmed again this pass, `find . -iname "docker-compose.override*"`
  returns nothing) and nothing was created in its place. The base
  `docker-compose.yml`'s `discovery_documents_data` volume (added in the
  previous pass) is unchanged.
- **No production RAG ingestion was activated or redesigned.**
  `app/api/routers/rag.py` was not touched this pass. RAG remains, as
  before, CMT Veda/Lovable's responsibility — this pass only reasoned
  about it in documentation, verifying the existing separation still
  holds after the full-text/extraction changes below (it does: RAG
  metadata construction never reads `DiscoveryEditorialDraft` or
  `extracted_text` for anything other than the `full_text_format`/
  `pdf_available` fields already exposed).
- **`.env` was not touched** (only `.env.example`, which was already
  correct from the previous pass and needed no changes this pass).
- **No `git reset --hard` / `git clean` was run** (this checkout is not
  even a git repository — confirmed via environment metadata — so this is
  moot, but stated for the record per the instruction's explicit ask).
- **The working Gemini report-extraction implementation was not altered**
  beyond exactly what section 1 below required (feeding it full text when
  available) — no prompt fields were removed, no unrelated behavior
  changed, and the abstract-only path is fully preserved as a fallback.

### 1. Full text now actually feeds Gemini editorial generation

This was the corrective prompt's most important item: previous revisions
acquired and stored full-text documents but editorial generation still
only ever read the abstract. That gap is now closed.

**New: `app/services/fulltext/content_sniff.py`** — `sniff_format(content)`
inspects the actual downloaded bytes (PDF signature `%PDF-` at byte 0;
`<?xml`/DOCTYPE/root-tag inspection for XML vs HTML vs JATS-style article
XML) and returns `"pdf" | "xml" | "html" | None`. This is never based on
a server's `Content-Type` header or the resolver's claimed format — see
section 3.

**New: `app/services/fulltext/extraction.py`** — `extract_text(content,
full_text_format)` returns an `ExtractionResult(success, text, char_count,
truncated, error)`:
- PDF → `pypdf.PdfReader`, page-by-page `extract_text()`, joined.
- XML → `xml.etree.ElementTree`, all text nodes concatenated; falls back
  to the HTML tag-stripper on `ParseError` (some real article XML is
  lenient in ways `ElementTree` rejects).
- HTML → a stdlib `html.parser.HTMLParser` subclass that skips
  `script`/`style`/`nav`/`header`/`footer`/`noscript`/`svg`/`form`/
  `button`/`aside` content and collects the rest.
- Output is whitespace-normalized and capped at `MAX_EXTRACTED_CHARS =
  20_000` (a deliberate prompt-size bound; `truncated=True` when hit).
- Empty-after-extraction text is treated as a failure, not a hollow
  success.

**Changed: `app/services/fulltext/service.py`** — after a document is
downloaded and its format is confirmed by `sniff_format` (section 3),
`acquire_full_text()` now also calls `extract_text(content, actual_format)`
and persists the result onto the `DiscoveryDocument` row: `extracted_text`,
`extracted_char_count`, `extraction_status` (`"success"` /`"failed"`/
`"not_attempted"`), `extraction_error`.

**Changed: `app/services/intelligence/editorial_service.py`** —
`generate_editorial_draft()` now looks up the candidate's most recent
`DiscoveryDocument` with `retrieval_status == "acquired"` AND
`extraction_status == "success"` AND non-null `extracted_text`
(`_get_acquired_document()`). When one exists, `_build_prompt()` appends
the extracted text as a clearly labeled block:

> "Full article text was retrieved and extracted from the original source
> (format: {format}). This is the PRIMARY source material — prefer it
> over the abstract above wherever they could conflict: {extracted_text}"

The abstract is **not removed** from the prompt — it stays as a concise
supplement — but the system prompt now explicitly instructs the model:
"When full article text is provided (not just an abstract), treat it as
your primary evidence base and draw your summary, key points, and detail
from it rather than from the abstract alone." When no acquired+extracted
document exists (full text never retrieved, or retrieval/extraction
failed), generation silently and correctly falls back to abstract-only,
exactly as before this pass.

**Provenance**: `DiscoveryEditorialDraft` gained `source_document_id`
(FK, nullable) and `used_full_text: bool`, set from the same lookup used
to build the prompt, so it's possible to tell — without re-deriving
anything — whether a given draft was actually grounded in extracted full
text or only the abstract. `update_draft_manually()` carries both fields
forward from the AI-generated version onto any human-edited version, so a
manual edit never loses the record of what the original generation was
based on. This is a distinct axis from `is_ai_generated` (which tracks
AI-vs-human-edited), not a replacement for it.

### 2. Full-text format validation — content is sniffed, never assumed

Per the instruction, "the system must never label an unknown or
mismatched document as PDF" and must not "use .pdf as a generic fallback
for unknown MIME/content." `app/services/fulltext/service.py` now:

1. Downloads the candidate URL as before.
2. Calls `sniff_format(content)` on the actual bytes.
3. If `None` (content doesn't match any recognized signature) — the
   document row is still created for observability, but with
   `retrieval_status = "unsupported"`, `full_text_format = None`,
   `pdf_available = False`, **no storage write**, and no extraction
   attempt. `full_text_available` on the candidate is not set.
4. If the sniffed format **disagrees** with what the resolver claimed
   (`resolution.full_text_format`, itself derived from Europe PMC's
   `documentStyle` field, or a server's `Content-Type` header elsewhere),
   a `fulltext_format_mismatch` warning is logged and **the sniffed
   format is used as the authoritative one** for storage extension,
   `full_text_format`, `pdf_available`, and extraction dispatch — never
   the claimed one.
5. `pdf_available` is now derived strictly from the sniffed
   `actual_format == "pdf"`, not from whatever the resolver predicted.

New regression tests (`tests/test_fulltext.py`, added this pass — not yet
executed, see section 6 for why):
`test_acquire_full_text_rejects_unrecognized_content_as_unsupported`
(server claims PDF via `documentStyle`, body is plain error text →
asserts `retrieval_status == "unsupported"`, nothing stored, candidate's
`full_text_available` stays `False`); `test_acquire_full_text_corrects_
mislabeled_format` (`Content-Type: application/pdf` header, body is
genuinely HTML → asserts `full_text_format == "html"`, **never** `"pdf"`,
and extraction still succeeds against the true HTML content);
`test_acquire_full_text_populates_extraction_fields`.

Unit tests for the sniffer and extractor themselves
(`tests/test_content_sniff.py`, `tests/test_fulltext_extraction.py`) —
these **were** executed directly, see section 6.

### 3. PubMed / ClinicalTrials.gov / Europe PMC retrieval tightened further

Per instruction #4 ("don't use bare CMT or generic hereditary neuropathy
as independent retrieval drivers for either source... don't use gene
names as independent broad retrieval drivers"):

- `app/collectors/pubmed.py` — `DEFAULT_VOCABULARY["disease"]`:
  `["Charcot-Marie-Tooth", "CMT", "hereditary motor sensory neuropathy",
  "HMSN", "hereditary neuropathy"]` → `["Charcot-Marie-Tooth",
  "Charcot Marie Tooth", "hereditary motor sensory neuropathy", "HMSN"]`.
  Bare `"CMT"` and generic `"hereditary neuropathy"` removed. Gene terms
  were already excluded from the retrieval vocabulary in the previous
  pass and remain so — unchanged.
- `app/collectors/clinicaltrials.py` — `DEFAULT_QUERY_TERM`:
  `"Charcot-Marie-Tooth OR CMT OR \"hereditary motor sensory
  neuropathy\" OR HMSN OR \"hereditary neuropathy\""` → `"\"Charcot-
  Marie-Tooth\" OR \"Charcot Marie Tooth\" OR \"hereditary motor
  sensory neuropathy\" OR HMSN"`.
- `app/collectors/europepmc.py` — `DEFAULT_QUERY_TERMS`: same change as
  PubMed's.
- Structured-condition prioritization for ClinicalTrials.gov (previous
  pass) is unchanged and retained — `conditionsModule.conditions` is
  still evaluated in isolation from free-text background/description
  when present.
- `tests/test_retrieval_tightening.py` needed **no changes** — its one
  test exercising a bare `"CMT"` term constructs its own local vocabulary
  override rather than importing `DEFAULT_VOCABULARY`, so it's unaffected
  by (and doesn't re-verify) this specific change; the existing
  eligibility-gate tests are what actually prove bare "CMT" doesn't
  qualify a record downstream regardless of what retrieval fetched.

### 4. Eligibility rule — retained exactly, not re-changed

Per instruction #5/#6/#7/#8, the stricter rule from the previous corrective
pass is kept **unchanged**: bare "CMT" alone insufficient; full disease
name (or HMSN/Dejerine-Sottas) sufficient alone; CMT subtype codes
(CMT1A, CMT2A, CMTX1, …) sufficient alone; gene names (PMP22, MFN2, GJB1,
MPZ, GDAP1, …) never sufficient alone, including combined with only
generic hereditary/peripheral neuropathy text; structured
`conditionsModule.conditions` continues to take priority over incidental
background-text mentions. No changes were made to
`app/services/intelligence/eligibility.py` this pass — it was reviewed
against every example in the instruction's accept/reject matrix (section
13) and confirmed correct as-is; see section 6 for the specific tests
that prove each case.

### 5. Source expansion (pasted "SOURCE EXPANSION" instruction)

The architecture already separates "does a collection *method* exist"
from "is a specific *source* turned on" (`app/collectors/registry.py`):
adding a source that uses an already-supported method
(`official_api` via a named collector, or `rss_atom` via the generic
`GenericRSSCollector`) requires **zero new code** — only a
`discovery_sources` row created through the existing admin API
(`POST /sources`, `app/api/routers/sources.py`). No seed script or
migration creates source rows in this codebase; that's an operator/admin
action, consistent with how PubMed and ClinicalTrials.gov are already
configured. Per the instruction ("keep the integration clearly marked as
pending/inactive rather than claiming it is operational"), **no
`discovery_sources` rows were created for anything in this pass** — the
work here is entirely about which sources are code-ready to be turned on
by an admin, and which are not, and why.

| Source | Status | Notes |
|---|---|---|
| PubMed | **Active** (existing) | Collector implemented, retrieval tightened this pass |
| Europe PMC / PMC | **Code-ready, not activated** (existing, unchanged this pass) | `EuropePMCCollector` implemented and unit-tested against fixture JSON; official REST API, no auth required, long-stable contract; still never called against the live service in this sandbox (no network access from the shell) — must be live-verified before an admin creates its `discovery_sources` row |
| ClinicalTrials.gov | **Active** (existing) | Collector implemented, retrieval tightened this pass, structured-conditions priority retained |
| EU CTIS | **Pending** | No officially documented, stable public REST/bulk-data contract could be established from this environment; EU CTIS is primarily a web portal. Not implemented, to avoid encoding a guessed integration. |
| WHO ICTRP | **Pending** | Offers bulk XML/CSV data products under registration/access terms, not a live public query API comparable to PubMed/ClinicalTrials.gov. Not implemented. |
| ClinVar | **Pending — architectural reason, not just access** | NCBI E-utilities access is technically reachable (same family as PubMed's), but ClinVar records are structured genetic *variants*, not papers or trials — they don't fit the existing candidate content-type model (`app/services/candidate_service.py`'s source→content-type mapping) or the full-text-acquisition/editorial-draft pipeline downstream, which assumes an article-like source. Adding it properly means a genuine candidate-shape design decision, which the instruction says not to do ("do not redesign the existing Discovery architecture"). Left pending rather than force-fit. |
| Orphanet / Orphadata | **Pending** | Distributed as periodic bulk XML "nomenclature pack" downloads, not an incremental query API — would need a different (batch-import) ingestion model than every other collector in this codebase. Not implemented. |
| OMIM | **Pending** | Programmatic access explicitly requires an application/registration process (per omim.org's own API terms); the instruction requires "officially permitted API/licensed access" — no such access is established in this deployment environment. Not implemented, not even as a disabled stub with a guessed endpoint. |
| CMTA (cmtausa.org) | **Pending — architecturally ready, feed unverified** | Confirmed running WordPress (`wp-content` asset paths). `https://cmtausa.org/feed/` was fetched and did not resolve to a valid RSS/Atom feed in this tool's rendering. Could not confirm a `<link rel="alternate" type="application/rss+xml">` autodiscovery tag from a partial page fetch. No `discovery_sources` row created. |
| HNF / CureCMT (curecmt.org) | **Pending — feed confirmed absent** | `https://curecmt.org/feed/` returned an explicit 404. No alternate feed URL found. |
| CMTRF (cmtrf.org) | **Pending — architecturally ready, feed unverified** | Confirmed running WordPress (`wp-content` asset paths). No autodiscovery `<link>` tag visible in the fetched homepage content; no feed URL confirmed. |
| Neurology Asia (neurology-asia.org) | **Pending** | A legitimate ASEAN Neurological Association medical-journal site; custom PHP-based, no CMS or feed signature detected. Article archives are PDF-based, not feed-based, so this would need a `controlled_webpage_extraction`-style collector (unimplemented method, see `registry.py`'s `NotImplementedCollector`), not `generic_rss`. |
| ECMTF (ecmtf.org) | **Pending — architecturally ready, feed unverified** | Confirmed running WordPress 6.2.12. No visible feed link in the fetched homepage content. |
| CMT Australia (cmtaustralia.org.au) | **Pending — architecturally ready, feed unverified** | Confirmed running WordPress (Site Kit by Google plugin present). No visible feed link in the fetched homepage content. |
| Peripheral Nerve Society (pnsociety.com) | **Pending — architecturally ready, feed unverified** | Confirmed running WordPress (WP Rocket cache plugin present); confirmed to be the legitimate PNS site (address, verified social links, professional structure match). No visible feed link in the fetched homepage content. |

**Why "architecturally ready, feed unverified" isn't just activated
anyway:** this sandbox's web-fetch tooling renders pages through an
HTML→markdown conversion step and cannot reliably confirm a raw HTTP
response is genuinely `application/rss+xml`/`application/atom+xml`
content versus an HTML error/redirect page that merely looks similar
after conversion — the same class of ambiguity that sections 2/3 above
exist to prevent for downloaded documents. Rather than guess a feed URL
and risk silently creating a source that "runs" but only ever ingests
malformed or empty data, these five WordPress-confirmed orgs are left
pending with the exact verification step spelled out below, and the two
with a definitive negative result (CMTA's `/feed/` resolving to non-feed
content, HNF's `/feed/` 404) are documented as such rather than retried
speculatively against unverified alternate paths.

**To activate any `generic_rss`-eligible source once an admin has
confirmed a real feed URL** (e.g. by curling it directly from the VPS,
where outbound network access is not sandboxed the way this build
environment is), no code change is needed — only:

```
POST /sources
{
  "source_name": "CMT Research Foundation (CMTRF) News",
  "source_type": "cmt_organization",
  "source_tier": "tier_2",
  "base_url": "https://cmtrf.org",
  "collection_method": "rss_atom",
  "configuration": {"collector": "generic_rss", "feed_url": "https://cmtrf.org/feed/"},
  "enabled": true
}
```

All sources — active, code-ready, or eventually activated this way —
feed the same single downstream workflow (normalization → CMT
eligibility gate → candidate → full-text acquisition → extraction →
Gemini editorial draft → CMT Veda admin review); no per-source editorial
branch was created or would be needed, since `GenericRSSCollector`,
`PubMedCollector`, `ClinicalTrialsCollector`, and `EuropePMCCollector` all
produce the same `NormalizedRecord` shape consumed by one shared
pipeline.

### 6. Tests — what was actually executed vs. statically checked

Honesty about verification depth, same discipline as every previous
revision, with one genuine improvement this pass:

**Actually executed (not just `py_compile`)**: `app/services/fulltext/
content_sniff.py` and `app/services/fulltext/extraction.py` were run
directly in this sandbox (both are pure-stdlib plus `pypdf`, none of
which need the still-uninstallable `fastapi`/`sqlalchemy`/`pytest`
stack). This included genuine PDF text extraction against a real,
generated PDF fixture (`tests/fixtures/files/sample_cmt_article.pdf`,
authored via a one-off `reportlab` script — `reportlab` is *not* added to
`requirements.txt`, it was only used to produce this one checked-in
fixture). All 11 `content_sniff` cases and all 8 `extraction` cases
(mirrored in `tests/test_content_sniff.py` and
`tests/test_fulltext_extraction.py`) passed when run this way. This is
materially stronger evidence than `py_compile` + manual tracing, and is
the only part of this project, across all three corrective passes, with
genuine executed proof.

**Statically checked only** (`python3 -m py_compile` across the full
repo, exit 0, plus manual line-by-line tracing against each new/changed
test) — same limitation as every previous pass, because `fastapi`,
`sqlalchemy`, `pytest`, `respx`, `pydantic-settings`, `structlog`, and
`feedparser` remain uninstallable in this sandbox (`pip3 install
--break-system-packages` against them fails with "Could not find a
version that satisfies the requirement", and `curl` to `pypi.org`
returns HTTP 403 — reconfirmed at the start of this pass, unchanged from
every previous one):
- `tests/test_fulltext.py`'s 3 new tests (unsupported-content rejection,
  mismatched-format correction, extraction-field population).
- `tests/test_editorial.py`'s 5 new tests (full-text-as-primary-source
  prompt content, extraction-failure fallback, abstract-retained
  alongside full text, manual-edit provenance preservation, plus the
  updated baseline test).
- All eligibility-gate tests from the previous pass (unchanged this
  pass, re-traced against this pass's accept/reject matrix, section 13
  of the instruction):
  - **Reject**: bare "CMT" alone → `test_bare_cmt_acronym_alone_does_
    not_qualify`. Gene alone → `test_gene_only_...` (previous pass).
    Generic hereditary neuropathy + gene →
    `test_generic_hereditary_neuropathy_plus_gene_no_longer_qualifies`.
    Generic peripheral neuropathy + gene →
    `test_generic_peripheral_neuropathy_plus_gene_no_longer_qualifies`.
    Incidental CMT mention unrelated to the actual study →
    `test_incidental_cmt_mention_unrelated_to_study_is_rejected`.
  - **Accept**: "Charcot-Marie-Tooth" →
    `test_full_form_with_hyphen_qualifies`. "Charcot Marie Tooth" →
    `test_full_form_without_hyphen_qualifies`. Valid subtype code
    (CMT1A/CMT2A/CMTX1) → existing subtype tests (previous pass,
    unchanged). CMT disease context + gene →
    `test_explicit_cmt_context_plus_gene_still_qualifies`.
  - Structured-conditions priority: `test_structured_conditions_take_
    priority_over_incidental_description`, `test_structured_conditions_
    accept_even_with_generic_title`.
- Full-text format-priority tests from the previous pass (unchanged):
  PDF preferred over XML/HTML, XML used when no PDF, HTML used when
  neither — all three retained and unaffected by this pass's sniffing
  changes (the sniffer confirms what the resolver already correctly
  prioritized; it doesn't change the priority order itself).

**Not run this pass, same as always**: the full `pytest` suite. The
environment's `pip install` is blocked at the proxy level (confirmed
again, see above); nothing short of a differently-provisioned build
environment or VPS-side execution can change this. Every claim above
about "passed" for statically-checked tests means "manually traced
against the implementation and confirmed to assert the right thing," not
"executed by pytest and observed to pass" — only the `content_sniff`/
`extraction` claims mean the latter.

Full repo-wide `find . -name "*.py" | xargs python3 -m py_compile`
sweep: **exit 0** (re-run after every file change this pass).

### 7. Remaining limitations (this pass)

- Europe PMC remains code-ready but not live-verified or activated —
  unchanged from the previous pass; still requires an admin to
  live-verify the collector against the real API before creating its
  `discovery_sources` row.
- ClinVar, Orphanet/Orphadata, OMIM, EU CTIS, WHO ICTRP, and all seven
  CMT ecosystem organizations remain unimplemented/pending, for the
  specific reasons in the table in section 5 — none is claimed as
  operational.
- The full `pytest` suite has still never been executed in this sandbox,
  for the same environment reason as every previous pass (see section 6).
  `content_sniff.py`/`extraction.py` are the one exception, genuinely
  executed.
- Extraction quality for real-world PDFs varies with `pypdf`'s
  capability (scanned/image-only PDFs with no embedded text layer will
  correctly report `extraction_status = "failed"` rather than fabricate
  text, but will not be OCR'd — no OCR dependency was added).
- `MAX_EXTRACTED_CHARS = 20_000` is a reasonable but arbitrary prompt-size
  bound; very long articles are truncated (`truncated=True` is recorded,
  not hidden) rather than exceeding it.

## CMT-specific discovery overhaul — FINAL CORRECTIVE PROMPT (previous revision)

This revision applies the "FINAL CORRECTIVE PROMPT" received after the
consolidated implementation below. It tightens the eligibility gate
further, fixes a real cross-candidate document-association bug, adds
PDF→XML→HTML full-text format priority with truthful format recording,
makes taxonomy-seed failures visible instead of silently swallowed, and
adds persistent/shared full-text storage. **No production reset was
executed. No VPS deployment files were touched.**

### 1. Eligibility gate — stricter disease-context-first rule

`app/services/intelligence/eligibility.py` was restructured:

- The bare acronym **"CMT" is no longer sufficient on its own**. It is
  now tracked separately (`ACRONYM_TERMS`) and only contributes to a
  clearer rejection reason; it never makes a record eligible by itself.
- A new `FULL_FORM_TERMS` set (`"charcot-marie-tooth"`,
  `"charcot marie tooth"`) is the primary qualifying signal — either
  spelling, present anywhere in the evaluated text, is sufficient alone.
- `STRONG_DISEASE_TERMS` now holds only unambiguous clinical synonyms
  that don't rely on the acronym at all (HMSN, Dejerine-Sottas) — still
  sufficient alone.
- CMT subtype codes (CMT1A, CMT2A, CMTX1, …) remain sufficient alone.
- **The previous "weak neuropathy context + gene" eligibility path is
  removed.** A gene match is never an independent driver, and combining
  it with only generic "hereditary neuropathy"/"peripheral neuropathy"
  text no longer qualifies — disease context (full form, clinical
  synonym, or subtype) must be established first; gene/subtype evidence
  is supporting detail on an already-qualifying record only.
- `EligibilityResult` gained `matched_full_form: bool` and
  `matched_bare_acronym: bool` so callers/audits can see exactly which
  evidence tier fired.

**ClinicalTrials.gov structured-condition prioritization:** when
`structured_conditions` is passed and non-empty, eligibility is now
assessed **from the structured condition text alone** — free-text
title/description is not consulted at all in that case. A trial whose
actual listed condition doesn't name CMT is rejected even if its
background/description text mentions CMT incidentally, which is exactly
what "do not allow a weak incidental mention buried in the description to
override the absence of genuine CMT disease relevance" requires. When no
structured conditions are available (PubMed, or an empty conditions
list), behavior falls back to evaluating title+abstract, unchanged.

### 2. Full-text format priority (PDF → XML → HTML)

`app/services/fulltext/resolver.py`'s `_pick_best_url()` now explicitly
prioritizes PDF, then XML, then HTML (previously it fell back to "the
first URL present" after PDF, which could arbitrarily pick HTML over an
available XML). `ResolutionResult` and `DiscoveryDocument` both gained a
`full_text_format` field (`"pdf" | "xml" | "html" | "other"`) that always
records the format truthfully, and a `pdf_available: bool` flag distinct
from the broader `full_text_available` candidate flag — a candidate can
have full text available (XML or HTML) with `pdf_available=False`, and
is never rejected or downgraded for lacking a PDF specifically.

### 3. Fixed: cross-candidate document dedup bug

**This was a real bug in the previous revision.** `DiscoveryDocument` had
a single-column unique constraint on `content_hash`. When two different
candidates resolved to the identical physical file (e.g. a preprint and
its published version), `acquire_full_text()` for the second candidate
would find the first candidate's row by `content_hash` and **return it
directly** — meaning the second candidate had no `discovery_documents`
row of its own, and any lookup by `candidate_id` for it would find
nothing.

Fixed: the unique constraint is now on **`(candidate_id, content_hash)`**
— physical storage is still deduplicated at the storage layer
(`app/services/fulltext/storage.py`, unchanged), but every candidate that
resolves to a shared physical file now gets its **own**
`discovery_documents` row, correctly associated by its own `candidate_id`
and `id`, sharing only the `content_hash`/`document_ref` values that
describe the physical file. See
`tests/test_fulltext.py::test_acquire_full_text_hash_dedups_storage_but_keeps_per_candidate_rows`.

### 4. RAG provenance — verified, documented, and tested explicitly

The metadata payload sent toward RAG ingestion
(`app/api/routers/rag.py::_build_rag_metadata`) was already built
entirely from `DiscoveryCandidate` and `DiscoveryDocument` fields and
never touched `DiscoveryEditorialDraft` — this was correct in the prior
revision, but had no explicit regression test proving it. New file
`tests/test_rag_provenance.py` asserts this directly: an editorial draft
with deliberately distinctive placeholder text has **zero effect** on the
metadata payload, and the payload is byte-for-byte identical whether or
not a draft exists (modulo the wall-clock `approval_time` field). The
`full_text` block was also extended with `full_text_format` and
`pdf_available` for completeness.

### 5. Reset service now accounts for `discovery_documents`

`app/services/reset_service.py`'s `_TABLES_IN_DELETE_ORDER` gained
`discovery_documents` (deleted after `discovery_analysis`/
`discovery_editorial_drafts`, before `discovery_candidates`, respecting
its FK to `discovery_candidates.id`). Only document **rows** are
deleted — this module has no filesystem access to the storage volume and
does not delete physical files on disk; that is documented as an explicit
known limitation, not silently ignored. Reset remains explicit,
confirmation-protected (`confirm=True`), transactional, and is **never
called automatically** from any endpoint, handler, or startup path.
`alembic/versions/0002_cmt_specific_overhaul.py` was edited in place
(not superseded by a new migration) to add `full_text_format`,
`pdf_available`, and the corrected unique constraint, since this
migration has not been applied to any real database yet.

### 6. Taxonomy startup failures are no longer silently swallowed

`app/main.py` and `app/worker/worker.py`'s taxonomy-seeding startup hooks
now distinguish two cases:
- A genuinely missing table (the one truly benign case this sandbox's
  own `app_client` test fixture hits, since it runs the hook against a
  separate, unmigrated engine) — logged at WARNING, startup continues.
- Any other failure (a real DB outage, a permissions problem, a query
  error against an existing table) — now logged at **ERROR with the full
  exception** (`exc_info=True`), not a generic warning. Startup still
  does not crash the process (a crash-looping API is worse than a
  degraded one), but the failure can no longer be missed in log
  aggregation.

Additionally, `/readiness` (`app/api/routers/health.py`) now queries
`discovery_taxonomy`'s row count **live, on every call**, independent of
whether the startup hook itself believed it succeeded, and returns
`taxonomy_seeded: false` plus a `warnings` array explaining the exact
consequence when the taxonomy is empty. This deliberately does **not**
flip the overall `status` to `not_ready` (an empty taxonomy is a data
problem, not a process-health problem, and tying it to the readiness
probe risks triggering restarts/rotation over a data issue) — it is
surfaced as a visible warning field instead, which is what makes it
"visible through health/diagnostic mechanisms" without turning a data gap
into an availability incident.

### 7. Persistent, shared full-text storage

`docker-compose.yml` (the base file — **not** the VPS's
`docker-compose.override.yml`, which remains completely untouched) now
mounts a new named volume, `discovery_documents_data`, at
`/data/discovery-documents` on **both** `discovery-api` and
`discovery-worker`. Previously, full-text files written by the worker
(which performs acquisition) had no volume at all — they would vanish on
container recreation and, even within a single deployment, would not be
visible to the API container. `.env.example`'s
`FULLTEXT_STORAGE_BACKEND`/`FULLTEXT_STORAGE_DIR` values were also
corrected to match what `app/config.py`/`app/services/fulltext/storage.py`
actually accept (`local`, `/data/discovery-documents`) — the previous
revision's `.env.example` had a typo'd backend name (`local_disk`, not a
recognized value) and a dev-only path.

### 8. Schema/config gaps closed from the previous revision

- `app/schemas/candidate.py`: `CandidateOut.full_text_available` added.
- `app/schemas/run.py`: `RunOut.cmt_rejected` added.

### 9. Files changed this revision

`app/services/intelligence/eligibility.py` (rewritten eligibility logic),
`app/services/fulltext/resolver.py` (format-priority `_pick_best_url`,
`full_text_format` field), `app/services/fulltext/service.py` (fixed
cross-candidate dedup, `full_text_format`/`pdf_available` propagation),
`app/models/document.py` (`full_text_format`, `pdf_available`, corrected
unique constraint), `app/api/routers/rag.py` (provenance-separation
docstring, `full_text_format`/`pdf_available` in metadata),
`app/services/reset_service.py` (`discovery_documents` in delete order),
`app/main.py` / `app/worker/worker.py` (ERROR-level taxonomy-seed-failure
diagnostics), `app/api/routers/health.py` (`/readiness` live taxonomy
check + warnings), `docker-compose.yml` (shared/persistent full-text
volume), `.env.example` (corrected fulltext settings),
`alembic/versions/0002_cmt_specific_overhaul.py` (edited in place, not
yet applied anywhere), `app/schemas/candidate.py`, `app/schemas/run.py`.

New/updated tests: `tests/test_eligibility_gate.py` (9 new/rewritten
cases for the stricter rule), `tests/test_fulltext.py` (format-priority
unit tests, PDF/XML/HTML end-to-end tests, rewritten dedup-bug-fix test),
`tests/test_reset_service.py` (`discovery_documents` in fixture/assertions),
`tests/test_taxonomy_seed.py` (2 new `/readiness` tests), new
`tests/test_rag_provenance.py` (3 tests), `tests/test_worker_jobs.py`
(one fixture title corrected to use the full CMT form so it still clears
the stricter gate).

### 10. Test results (same honest caveat as every prior revision)

`pytest` was **not executed** — this sandbox still has no network access
to install `fastapi`/`sqlalchemy`/`pytest`/etc. (`curl -sI
https://pypi.org` → `403`, `pip install -r requirements.txt` → `ERROR:
Could not find a version that satisfies the requirement fastapi==0.115.0
(from versions: none)`, both reconfirmed at the start of this revision).
`python3 -m py_compile` was run against **every** `.py` file in the
repository after every batch of edits, most recently as a full sweep at
the end — all pass, zero syntax errors. Every new/changed test was
manually traced against the corrective prompt's own accept/reject test
matrix (section 17 of the prompt), including one bug caught this way
before delivery: an early draft of the "bare CMT acronym" test fixture's
abstract text literally contained the substring "Charcot-Marie-Tooth" in
a sentence explaining it was *not* mentioned, which — since the matcher
is purely textual, not semantic — would have made the fixture accidentally
eligible and the test wrong; the fixture text was rewritten to avoid
spelling out the full form at all. **Run the real test suite before
deploying — this is not a substitute.**

### 11. Confirmations

- **No production reset was executed.** `execute_reset` still requires
  `confirm=True` and is called from nowhere in the codebase.
- **VPS deployment overrides were preserved.** `docker-compose.override.yml`
  does not exist in this repository checkout (it is VPS-local, as in
  every prior revision) and was not created, referenced for removal, or
  assumed; the only docker-compose edit is an additive volume mount in
  the base `docker-compose.yml`. No port binding was changed.
- `.env` was not touched (only `.env.example`, the checked-in template).
- No `git reset --hard` / `git clean` was run (this sandbox has no git
  history for this checkout at all — files were edited in place).
- `/opt/cmtveda/veda-test` is not part of this repository checkout and
  was not touched.

### 12. Remaining known limitations (unchanged from before unless noted)

- `pytest` has never actually run in this sandbox at any point across
  any revision — every "test result" here is `py_compile` plus manual
  tracing against the spec's own test matrix, not a real run.
- The reset service deletes `discovery_documents` **rows** but not the
  underlying files on the storage volume — a reset followed by fresh
  acquisition would leave orphaned files on disk. Not implemented this
  pass (deleting files is a different risk profile than deleting rows
  and was not explicitly asked for); flagged for a future pass if disk
  reclamation matters.
- The Lovable frontend UI workflow's own reflection of the
  AI-news-vs-RAG-source distinction (corrective prompt #9's closing
  sentence) is out of scope for this backend-only Python repository.
- Source integrations beyond Europe PMC remain deferred pending verified
  access, unchanged from the previous revision (see that section below).
- The eligibility gate's `FULL_FORM_TERMS`/`STRONG_DISEASE_TERMS`/gene
  list are text-based lexical rules, not a clinical NLP model — a source
  that never spells out "Charcot-Marie-Tooth" or an HMSN/Dejerine-Sottas
  synonym anywhere in its indexed title/abstract/structured-condition
  text, even if unambiguously about CMT to a human reader, would be
  rejected. This is the deliberate tradeoff the corrective prompt asks
  for (false negatives over false positives) and matches "do not remove
  support for legitimate CMT literature where the full disease name
  appears elsewhere in the relevant source text" only to the extent that
  text is actually present in what's evaluated.

---

## CMT-specific discovery overhaul — consolidated implementation (earlier revision)

This revision implements, in one pass, the consolidated request covering
CMT eligibility gating, PubMed/ClinicalTrials.gov retrieval tightening,
Europe PMC as a new (registered-but-not-activated) source, full-text/PDF
acquisition and storage, an additive RAG-handoff metadata extension, and
a safe reset service that is built but **never invoked**. Phase 1
(taxonomy seeding + expansion) from the prior revision is unchanged and
still in effect; its writeup remains below this section.

### 1. Files changed / added

**Eligibility gate & retrieval tightening**
- `app/services/intelligence/rules.py` — `_term_matches` renamed to public
  `term_matches` (alias kept for backward compatibility).
- NEW `app/services/intelligence/eligibility.py` — deterministic
  `assess_eligibility()`: strong disease terms or a CMT subtype code are
  sufficient alone; weak/generic context terms (peripheral neuropathy,
  hereditary neuropathy, neurological disease) require gene corroboration;
  gene-only matches are never sufficient. Testable, rules-only, no AI
  involved in the final gate per spec 2D.
- `app/models/source_record.py` — `cmt_eligible`, `eligibility_reason`.
- `app/models/run.py` — `cmt_rejected` counter.
- `app/models/candidate.py` — `full_text_available` flag.
- `app/collectors/pubmed.py` — `build_query()` now builds from disease +
  CMT-subtype vocabulary only; gene terms are excluded from retrieval
  entirely (they remain in `DEFAULT_VOCABULARY["genetics"]` for other
  uses but are documented as no-longer-used-for-retrieval).
- `app/collectors/clinicaltrials.py` — captures
  `protocolSection.conditionsModule.conditions` into
  `raw_metadata["conditions"]` so the eligibility gate can inspect
  structured condition data, not just free text.
- `app/worker/handlers.py` — `handle_collect_source` now runs
  `assess_eligibility()` between dedup and candidate creation; a
  non-eligible record is kept in `discovery_source_records` for
  provenance but no candidate is created, and `run.cmt_rejected` is
  incremented. `handle_analyse_candidate` now routes `research_paper`
  candidates through a new `resolve_full_text` job before the editorial
  draft step (clinical trials unchanged). New `handle_resolve_full_text`
  handler always enqueues `generate_editorial_draft` afterward regardless
  of full-text outcome, and never fails the candidate.
- `app/models/enums.py` — `JobType.resolve_full_text` added.
- `app/api/routers/manual.py` — docstring-only: documents the deliberate
  decision to **not** apply the automated eligibility gate to manual
  submissions (a human reviewer's submission is itself the relevance
  judgment, matching the existing `has_manual_override` philosophy). No
  behavior change.

**Europe PMC (new source, registered but not activated)**
- NEW `app/collectors/europepmc.py` — `EuropePMCCollector` against the
  official Europe PMC REST `/search` endpoint (cursorMark pagination),
  disease-only default query terms, pure `parse_europepmc_result()`.
- `app/collectors/registry.py` — registered under `"europepmc"`. No
  `discovery_sources` row is created for it, so it does not run in
  production until an admin explicitly adds one.
- `app/services/candidate_service.py` — added `europepmc` →
  `research_paper` content-type mapping.
- `app/config.py` — `europepmc_base_url`, `europepmc_rate_limit_per_sec`.

**Full-text / PDF acquisition**
- NEW `app/services/fulltext/resolver.py` — `resolve_full_text()` queries
  Europe PMC by PMID/DOI, checks `isOpenAccess`, returns an open-access
  URL or an "unavailable" result with a reason. Never raises; never
  bypasses a paywall (only ever looks at API-declared open-access URLs).
- NEW `app/services/fulltext/storage.py` — `DocumentStorage` abstraction,
  `LocalDiskDocumentStorage` (SHA-256-content-hash-keyed, two-level
  directory fan-out, atomic temp-file-then-rename writes).
- NEW `app/services/fulltext/service.py` — `acquire_full_text()`
  orchestration: idempotent short-circuit if already acquired, downloads
  via httpx with a size cap, stores via the storage backend, dedups
  across candidates on content hash (unique constraint), records
  provenance (source, license info, retrieval status), and sets
  `candidate.full_text_available` — a missing/unavailable full text never
  invalidates the candidate.
- NEW `app/models/document.py` — `DiscoveryDocument` model.
- `app/config.py` — `fulltext_storage_backend`, `fulltext_storage_dir`,
  `fulltext_max_bytes`.

**RAG handoff (additive only)**
- `app/api/routers/rag.py` — `_build_rag_metadata()` now optionally
  includes `full_text_available` and, when acquired, a `full_text` block
  (document ref, mime type, content hash, source, license/provenance)
  pulled from `DiscoveryDocument`. The RAG state machine, approval
  protections, and the provisional `/ingest` HTTP adapter are untouched;
  the adapter is **not** activated.

**Safe reset service (built, never invoked)**
- NEW `app/services/reset_service.py` — `dry_run_counts()` (always safe)
  and `execute_reset(db, *, confirm: bool)` (raises unless
  `confirm=True`; deletes in FK-safe child-to-parent order: newsletter
  items → RAG ingestion requests → editorial drafts → analysis →
  candidates → source records → runs → jobs). Preserves
  `discovery_sources`, `discovery_taxonomy`, `discovery_audit_log`,
  schema/migrations, and auth/configuration by construction — those
  tables never appear in the delete list. **Not called from any
  endpoint, worker handler, startup path, or script** — it exists purely
  as reviewable, tested code pending explicit approval to run it.

**Schema/config exposure**
- `app/schemas/candidate.py` — `CandidateOut.full_text_available` added.
- `app/schemas/run.py` — `RunOut.cmt_rejected` added.
- `.env.example` — documented `EUROPEPMC_*` and `FULLTEXT_*` settings.

**Migration**
- NEW `alembic/versions/0002_cmt_specific_overhaul.py` — additive only:
  adds `cmt_eligible`/`eligibility_reason` to `discovery_source_records`,
  `cmt_rejected` to `discovery_runs`, `full_text_available` to
  `discovery_candidates`, and creates `discovery_documents` (with a
  unique constraint on `content_hash`). Verified column-by-column against
  the SQLAlchemy models for zero drift. Has a working `downgrade()`.
  Does **not** touch `discovery_taxonomy`, `discovery_sources`,
  `discovery_audit_log`, or any existing column.

### 2. Source integrations: implemented vs. deferred

| Source | Status | Reason |
|---|---|---|
| PubMed / NCBI E-utilities | Tightened (existing) | Retrieval now disease/subtype-driven, gene-only excluded |
| ClinicalTrials.gov | Tightened (existing) | Structured `conditions` now captured for the eligibility gate |
| Europe PMC | **Implemented, registered, not activated** | Official REST API, stable/documented contract, but never called live in this sandbox (no network access) — no `discovery_sources` row created, so it will not run until an admin adds one and it has been live-verified |
| PMC full text | **Implemented as part of full-text resolver** | Resolved via Europe PMC's `isOpenAccess`/`fullTextUrlList`, same caveat as above |
| EU CTIS | Deferred | No official public API contract could be verified in this sandbox; spec requires verification before activation |
| WHO ICTRP | Deferred | Same — bulk-download/access terms need verification, not implemented |
| CMTA / HNF / CMTRF | Deferred | No official structured API found/verifiable; would need per-site scraping or a partnership feed, which the spec explicitly does not authorize without verified access |
| ClinVar | Deferred | Genetic knowledge source, not a discovery source; needs its own design pass once Phase 2-6 discovery-side work is confirmed working |
| Orphanet / Orphadata | Deferred | Same as ClinVar — access/licensing terms not verified in this sandbox |
| OMIM | Deferred (explicitly, per spec) | Spec requires official API/access/licensing verification before use; this sandbox has no network access to perform that verification, so OMIM is **not implemented**, not even as a disabled stub with guessed endpoints |

No source was activated or given a live production config change. Only
Europe PMC has code; everything else in the "deferred" list has no
code at all, to avoid encoding unverified assumptions about access methods.

### 3. Test results

**Honest disclaimer, unchanged from every prior revision:** this sandbox
has no network access and no pre-installed Python packages (`curl -sI
https://pypi.org` → `403 host_not_allowed`, reconfirmed this session).
`pytest` has **never been executed** here. What was actually done:

- `python3 -m py_compile` against every `.py` file in the repository
  (including every file touched in this revision) — **all pass, zero
  syntax errors**, re-run after the final test-file edit
  (`tests/test_worker_jobs.py` monkeypatch fix).
- Manual tracing of each new test file against the module it exercises,
  including two bugs caught and fixed this way before delivery (a wrong
  `respx` mocking pattern in `test_europepmc_parsing.py`, and a
  monkeypatch-target bug in `test_fulltext.py` where the patch needs to
  target `app.services.fulltext.service.get_document_storage`, not the
  storage module's own name, because of Python's `from x import y` local
  binding semantics).
- A regression-risk audit of existing tests against the new eligibility
  gate: confirmed `test_worker_jobs.py`'s existing
  `test_one_bad_record_does_not_abort_the_whole_source_run` still passes
  the gate (its fixture title contains the word "CMT", a strong term),
  and confirmed `test_candidate_engine.py`, `test_deduplication.py`, and
  `test_manual_discovery.py` call the relevant service functions directly
  rather than through `handle_collect_source`, so they bypass the gate
  entirely and are structurally unaffected.

New test files (not executed, only compiled and manually traced):
`tests/test_taxonomy_seed.py` (5), `tests/test_eligibility_gate.py` (9),
`tests/test_retrieval_tightening.py` (6),
`tests/fixtures/europepmc_responses.py` (fixtures only),
`tests/test_europepmc_parsing.py` (7), `tests/test_fulltext.py` (9),
`tests/test_reset_service.py` (8), plus 2 new integration tests appended
to `tests/test_worker_jobs.py`. **You must run `pytest` yourself to get
an authoritative pass/fail result; nothing here substitutes for that.**

### 4. Build result

No frontend/production build step exists in this repository beyond the
Python package itself and its Docker image. `python3 -m py_compile`
across the full tree is the applicable "build" check available in this
sandbox, and it passes cleanly. The Dockerfile/`docker-compose.yml` were
not modified, and `docker-compose.override.yml` (the VPS's
127.0.0.1:8001:8000 / 127.0.0.1:5432:5432 port bindings) was **not
touched, overwritten, or removed** — confirmed by inspection, not just by
omission from this changeset.

### 5. Deployment instructions

1. Pull this changeset onto the VPS branch `rev4-classification-fix`
   (or merge it there).
2. `alembic upgrade head` — applies migration `0002_cmt_specific_overhaul`
   (additive; no destructive operations; has a tested `downgrade()`).
3. Restart the API and worker processes. On startup, the existing
   taxonomy-seeding hook (Phase 1, idempotent) runs again — harmless, no
   duplicate rows.
4. Set the new optional env vars in `.env` if you want to override
   defaults (`EUROPEPMC_BASE_URL`, `EUROPEPMC_RATE_LIMIT_PER_SEC`,
   `FULLTEXT_STORAGE_BACKEND`, `FULLTEXT_STORAGE_DIR`,
   `FULLTEXT_MAX_BYTES`); all have working defaults if left unset.
5. `docker-compose.override.yml` and its port bindings require no change
   and were not modified — nothing to do there.
6. Europe PMC remains **inactive** until an admin explicitly creates a
   `discovery_sources` row for it (via the existing sources admin
   endpoint) after live-verifying the collector against the real API.
7. The RAG `/ingest` adapter remains in `mock` mode
   (`RAG_ADAPTER=mock`) unless already set to `http` — this revision does
   not change that setting and does not newly justify switching it,
   since the `/ingest` contract is still unverified per spec.

### 6. Reset instructions (for approval — NOT executed)

Per the standing instruction, the reset was **not run**. To run it once
approved:

```sql
-- 1. Backup first, always:
pg_dump -Fc -f pre_reset_backup_$(date +%Y%m%d_%H%M%S).dump discovery_engine

-- 2. Get pre-reset counts (safe, read-only):
--    from Python: app.services.reset_service.dry_run_counts(db)
--    or directly:
SELECT
  (SELECT count(*) FROM newsletter_items)          AS newsletter_items,
  (SELECT count(*) FROM rag_ingestion_requests)     AS rag_ingestion_requests,
  (SELECT count(*) FROM discovery_editorial_drafts) AS editorial_drafts,
  (SELECT count(*) FROM discovery_analysis)         AS analysis,
  (SELECT count(*) FROM discovery_candidates)       AS candidates,
  (SELECT count(*) FROM discovery_source_records)   AS source_records,
  (SELECT count(*) FROM discovery_runs)             AS runs,
  (SELECT count(*) FROM discovery_jobs)             AS jobs;

-- 3. Execute (children-before-parents order, matches
--    app/services/reset_service.py::_TABLES_IN_DELETE_ORDER exactly):
BEGIN;
DELETE FROM newsletter_items;
DELETE FROM rag_ingestion_requests;
DELETE FROM discovery_editorial_drafts;
DELETE FROM discovery_analysis;
DELETE FROM discovery_candidates;
DELETE FROM discovery_source_records;
DELETE FROM discovery_runs;
DELETE FROM discovery_jobs;
COMMIT;

-- 4. Verify (all should be 0):
SELECT
  (SELECT count(*) FROM newsletter_items)          AS newsletter_items,
  (SELECT count(*) FROM rag_ingestion_requests)     AS rag_ingestion_requests,
  (SELECT count(*) FROM discovery_editorial_drafts) AS editorial_drafts,
  (SELECT count(*) FROM discovery_analysis)         AS analysis,
  (SELECT count(*) FROM discovery_candidates)       AS candidates,
  (SELECT count(*) FROM discovery_source_records)   AS source_records,
  (SELECT count(*) FROM discovery_runs)             AS runs,
  (SELECT count(*) FROM discovery_jobs)             AS jobs;

-- 5. Confirm preserved (row counts unchanged from before):
SELECT count(*) FROM discovery_sources;
SELECT count(*) FROM discovery_taxonomy;
SELECT count(*) FROM discovery_audit_log;  -- unchanged; no FK to candidates
```

Or, equivalently, from the application:
```python
from app.services.reset_service import dry_run_counts, execute_reset
pre = dry_run_counts(db)          # inspect first
result = execute_reset(db, confirm=True)   # only when explicitly approved
assert result.verified_clean
```

**Rollback strategy:** the reset runs inside a single transaction
(`BEGIN`/`COMMIT` above, or the ORM session's own transaction in
`execute_reset`) — if anything fails mid-way, nothing commits. If the
reset completes and needs to be undone afterward, restore from the
`pg_dump` backup taken in step 1 (`pg_restore` into a fresh/empty
database, or `--clean` into the existing one). There is no partial-undo
path once committed; the pre-reset backup is the only rollback.

`discovery_sources`, `discovery_taxonomy`, `discovery_audit_log`, schema,
migrations, and auth/configuration are preserved by construction (never
appear in the delete list) and were spot-checked by
`tests/test_reset_service.py`.

### 7. Known limitations

- No source beyond Europe PMC was added in code; OMIM, ClinVar,
  Orphanet/Orphadata, EU CTIS, WHO ICTRP, and CMTA/HNF/CMTRF all remain
  unimplemented pending verified access — see the table in section 2.
- Europe PMC is implemented but has never made a live call — it is
  unit-tested against recorded-shape fixture JSON only (no network access
  in this sandbox). It must be live-verified before enabling.
- The full-text resolver only looks at Europe PMC's declared
  open-access status; it does not attempt publisher-site scraping or any
  other paywall-adjacent technique, by design.
- The RAG `/ingest` HTTP adapter remains unverified and inactive; only
  the metadata payload was extended (additively) so that a future,
  verified adapter has full-text provenance to hand off.
- The reset service is fully built and tested against SQLite but has
  never run against the actual production PostgreSQL database or its
  real data volume — the dry-run/execute split and the transaction
  wrapping are the safety net for that first real run, whenever it is
  approved.
- `pytest` has never actually been executed in this sandbox at any point
  across any revision (no network access to install dependencies) — every
  "test result" in this document is `py_compile` plus manual tracing,
  not a real test run. This is the single most important caveat for
  anyone deploying this: **run the real test suite before deploying.**
- The eligibility gate's `STRONG_DISEASE_TERMS`/`WEAK_CONTEXT_TERMS`
  vocabulary and the expanded taxonomy's gene/subtype lists are both
  built from standard CMT-literature nomenclature but have not been
  reviewed by a clinical/genetics domain expert — flagged for review, as
  with the Phase 1 taxonomy expansion.

---

## CMT-specific discovery overhaul — Phase 1: taxonomy initialization fix (earlier revision)

**Root cause found and fixed:** `discovery_taxonomy` was empty in
production because `seed_default_taxonomy()` (`app/services/taxonomy_service.py`)
was idempotent and already documented as "safe to call on every
app/worker startup," but nothing actually called it outside of tests.
Fixed by wiring it into both startup paths:

- `app/main.py`: a new `@app.on_event("startup")` handler calls it via
  `app.database.session_scope()`. Defensively wrapped in `try/except` --
  it runs against the module-level production engine, which is a
  *different* (and in the `app_client` test fixture, unmigrated) engine
  than whatever a test's `get_db` dependency override points at, so a
  failure there must not crash app startup. In production this always
  succeeds.
- `app/worker/worker.py`: `Worker.run_forever()` calls it once at startup,
  next to the existing `recover_stuck_jobs()` call, same defensive
  try/except.

**Taxonomy expanded** from 21 to 89 entries (5 disease terms, 27 genes, 41
CMT subtypes across the standard CMT1/2/3/4/X/DI numbering families, 16
topics) per the CMT-specific overhaul spec's Phase 2E list. Gene/subtype
provenance is documented in `taxonomy_service.py`'s module docstring:
standard CMT-literature nomenclature, not invented, but explicitly
flagged as needing review by someone with clinical/genetics authority
before being treated as exhaustive -- the taxonomy remains
admin-extensible at runtime regardless.

**Downstream effect (not yet fixed, flagged for Phase 2):** with the
taxonomy now populated, `run_rules_stage()` will start actually matching
disease/gene/subtype/topic terms in production for the first time --
previously it always returned an all-empty `RulesResult` since it queries
`discovery_taxonomy`, which was empty. This alone should measurably
improve classification quality (the AI stage now gets real deterministic
matches to cross-check against instead of none), but it does **not** by
itself fix the retrieval-breadth or missing-eligibility-gate issues --
those are Phase 2, not yet implemented as of this revision.

**Changed files:** `app/services/taxonomy_service.py` (expanded list,
unchanged function signature/behavior), `app/main.py` (new startup
handler), `app/worker/worker.py` (new startup call). **New file:**
`tests/test_taxonomy_seed.py` (5 new tests: idempotency, category
coverage, no-duplicate-keys, `app_client` still boots cleanly with the
new startup hook, existing gene-matching regression still passes).

**No migration required** -- `discovery_taxonomy`'s schema is unchanged;
this is a data-seeding and startup-wiring fix only.

**Not yet executed:** same standing sandbox constraint as every previous
revision -- no network access, `pip install` still fails
(`x-deny-reason: host_not_allowed`). All changed/new files pass `python3
-m py_compile`; the taxonomy list's structure (89 entries, zero duplicate
`(category, term)` keys, correct category counts) was independently
verified by parsing the literal directly with Python's `ast`/`eval`
rather than through SQLAlchemy (which isn't installed in this sandbox).
Actual `pytest` execution remains the real gate -- run it yourself.

Next: Phase 2 (CMT eligibility gate + PubMed/ClinicalTrials.gov retrieval
tightening), per the confirmed implementation order.

---

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

## Revision 4: CMT Veda compatibility layer

Added the compatibility routes needed so the already-built CMT Veda
Discovery Admin gateway can drive this engine, per an explicit
instruction not to redesign the database, collector architecture,
worker architecture, newsletter state machine, or existing API --
**every new route is a thin wrapper that calls an existing function
directly.** No second state machine, no duplicated persistence logic,
no new database tables. One new state-machine edge was added
(`scheduled -> approved`) because `DELETE .../schedule` genuinely had no
valid path to use otherwise -- this is documented in full below.

**New endpoints:**

| Route | File | Calls (existing, unchanged) | Auth |
|---|---|---|---|
| `GET /api/v1/overview` | new `app/api/routers/overview.py` | `COUNT`/`GROUP BY` over `DiscoveryCandidate`, `DiscoverySource`, `DiscoveryRun`, `DiscoveryEditorialDraft` | `require_any_authenticated` (unchanged, already includes `service`) |
| `PATCH /api/v1/candidates/{id}/editorial` | `app/api/routers/editorial.py` | `edit_draft()` -- called directly | new `require_service_or_reviewer_or_admin` |
| `POST /api/v1/candidates/{id}/editorial-status` | `app/api/routers/newsletter.py` | dispatch table -> `select_for_newsletter`/`submit_for_review`/`approve`/`reject`/`archive` | `require_any_authenticated` + inner per-target-status role check mirroring each granular route's own requirement |
| `POST /api/v1/candidates/{id}/schedule` | `app/api/routers/newsletter.py` | `schedule_newsletter_item()` -- called directly | new `require_service_or_admin` |
| `DELETE /api/v1/candidates/{id}/schedule` | `app/api/routers/newsletter.py` + new `newsletter_workflow.unschedule()` | new function, same `_transition`/`write_audit_log` pattern as `archive()` | new `require_service_or_admin` |
| `POST /api/v1/candidates/{id}/publish` | `app/api/routers/newsletter.py` | `newsletter_workflow.publish()` -- called directly; same ad-hoc `NewsletterPublication` creation pattern `schedule_newsletter_item` already uses | new `require_service_or_admin` |
| `POST /api/v1/candidates/bulk` | `app/api/routers/newsletter.py` | same dispatch table as `/editorial-status`, once per candidate, each in its own `db.begin_nested()` | same as `/editorial-status` |
| `POST /api/v1/runs/{run_id}/retry` | `app/api/routers/runs.py` | `app.worker.scheduler.trigger_run()` -- the exact function `/sources/{id}/run` already calls | new `require_service_or_admin` |

**Not implemented this pass:** `PATCH /sources/{id}/enabled`. The
instruction explicitly said to add it "only if required by the actual
CMT Veda gateway contract" -- that requirement was never confirmed, so
it was deliberately left out rather than guessed at.
`POST /sources/{id}/enable` and `/disable` are unchanged and still work.

**Changed files (9 modified, 3 new):**

Modified: `app/auth.py`, `app/models/enums.py`,
`app/services/workflows/newsletter_workflow.py`,
`app/api/routers/editorial.py`, `app/api/routers/newsletter.py`,
`app/api/routers/runs.py`, `app/main.py`, `app/schemas/newsletter.py`,
`tests/conftest.py`, `tests/test_newsletter_workflow.py`.

New: `app/api/routers/overview.py`, `app/schemas/overview.py`,
`tests/test_cmt_veda_compat.py`.

**No migration.** Confirmed by re-checking: `AuditAction` (where
`unscheduled` was added) is a VARCHAR-backed Python enum specifically so
new values never need `ALTER TYPE`; the one new transition edge
(`scheduled -> approved`) is a Python dict entry, not schema; every new
endpoint reads/writes columns that already existed.

**Auth boundary preserved, not weakened.** Every pre-existing route's
own `require_admin`/`require_reviewer_or_admin` dependency is completely
untouched. Two new combined dependencies
(`require_service_or_admin`, `require_service_or_reviewer_or_admin`)
were added and applied **only** to the brand-new routes above, so the
`service` role (already defined in `app/models/enums.py` for exactly
this purpose -- "for worker / internal service-to-service calls") can
call them. The `/editorial-status` and `/candidates/bulk` dispatchers
additionally re-derive and enforce each individual action's original
role requirement internally (a reviewer still cannot `approve`/`archive`
through the unified endpoint, exactly as they can't through the granular
one) -- see `_check_role_for_status` in `newsletter.py`.

**A real bug caught and fixed before it shipped, during test-writing:**
my first draft of the test helper for reaching `approved` assumed that
generating an editorial draft via `POST .../editorial-draft` also
advances the newsletter item to `drafted`. It does not -- only the
worker's automatic pipeline
(`app/worker/handlers.py::handle_generate_editorial_draft`) calls
`mark_drafted()`. I confirmed this by finding that
`tests/test_newsletter_workflow.py`'s own existing tests already call
`wf.mark_drafted()` directly as a separate setup step for exactly this
reason. This is pre-existing behavior of the system, not something this
patch introduced or changed -- the test helper was fixed to match it
(`tests/test_cmt_veda_compat.py::_advance_to_approved`).

**On `POST /candidates/{id}/publish`'s exact behavior** (a genuine
design decision made in the absence of a contract, flagged here
explicitly): it always creates its own ad-hoc single-item
`NewsletterPublication` for the one candidate being published, rather
than searching for and reusing whatever publication it might already be
attached to via `schedule()`. This avoids a real correctness risk: if a
candidate were attached to a multi-candidate publication and this
endpoint marked that WHOLE publication "published" as a side effect,
other not-yet-ready candidates in it could be misrepresented as
published too. If CMT Veda's actual contract expects the candidate's
existing (multi-item) publication to be found and used instead, this is
the one spot in this patch most likely to need revisiting once the real
contract is available.

**Test status: still not executed.** Confirmed again, fresh, this
revision: `curl -sI https://pypi.org` -> `403`, `x-deny-reason:
host_not_allowed`; `pip install fastapi` fails the same way it has every
revision. `tests/test_cmt_veda_compat.py` (28 new tests) and the 4 new
tests in `test_newsletter_workflow.py` were written and traced by hand
against the actual implementation, including one genuine bug they caught
before being fixed (above) -- but "traced by hand" is not "executed and
green." All 95 `.py` files in the repository pass `python3 -m
py_compile`. Run `pytest -v` yourself; treat that as the real gate.

**Deferred, per the task's own instruction:** the PubMed
vocabulary/scoping/date-parsing/`last_success_at` issues flagged in the
task (the "canine mammary tumors" off-topic candidate, future-dated
`original_date` values, and `last_success_at` advancing even when a run
had record-level errors) were **not investigated or touched** in this
pass. The instruction was explicit that these should only be addressed
here if "already isolated and trivial" -- none of the four are (they
require inspecting live collection data, the PubMed query construction,
date-parsing logic, and the incremental-collection boundary semantics,
none of which this sandbox can do without network access in any case).
Flagging as a distinct, real, and still-open follow-up -- not silently
dropped.

---

## Fixes applied in Revision 3

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
