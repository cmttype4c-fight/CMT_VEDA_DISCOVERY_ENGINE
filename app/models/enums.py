"""
Controlled vocabularies (enums) used throughout the Discovery Engine.

These are intentionally plain Python enums mapped as non-native SQL enums
(VARCHAR + CHECK constraint) rather than native Postgres ENUM types. This
keeps Alembic migrations simple (no ALTER TYPE ... ADD VALUE dances when the
taxonomy grows) and keeps the schema portable across SQLite (tests) and
Postgres (production), per spec #4 / #55.

NOTE: `discovery_taxonomy` (see models/taxonomy.py) is the place for truly
open-ended, admin-extensible vocabulary (genes, subtypes, topics -- spec
#9). The enums below are structural/workflow vocabulary that the engine's
logic depends on, so they are code-defined, not data-defined.
"""
import enum


class SourceType(str, enum.Enum):
    api = "api"
    rss = "rss"
    structured_feed = "structured_feed"
    web_extraction = "web_extraction"
    manual = "manual"


class SourceTier(str, enum.Enum):
    tier_1 = "tier_1"  # Primary scientific / clinical
    tier_2 = "tier_2"  # CMT specialist
    tier_3 = "tier_3"  # Institutional
    tier_4 = "tier_4"  # Journals / publishers
    tier_5 = "tier_5"  # Industry
    tier_6 = "tier_6"  # Recognised community


class CollectionMethod(str, enum.Enum):
    official_api = "official_api"
    rss_atom = "rss_atom"
    structured_feed = "structured_feed"
    controlled_webpage_extraction = "controlled_webpage_extraction"
    manual_submission = "manual_submission"


class RunStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    partial = "partial"
    failed = "failed"
    cancelled = "cancelled"


class RecordDedupeStatus(str, enum.Enum):
    new = "NEW"
    duplicate = "DUPLICATE"
    updated = "UPDATED"


class ContentType(str, enum.Enum):
    research_paper = "research_paper"
    clinical_trial = "clinical_trial"
    research_news = "research_news"
    clinical_guidance = "clinical_guidance"
    management_evidence = "management_evidence"
    nutrition = "nutrition"
    physiotherapy_rehabilitation = "physiotherapy_rehabilitation"
    assistive_technology = "assistive_technology"
    drug_therapy_update = "drug_therapy_update"
    conference_event = "conference_event"
    organisation_update = "organisation_update"
    community_resource = "community_resource"
    # FINAL FOCUSED CORRECTION, TASK 5: a ClinVar record is a genetic
    # variant/classification, not an article, trial, or news item -- it
    # has no abstract/PDF/full text (see app/collectors/clinvar.py's
    # module docstring). Routed straight to editorial-draft generation
    # without a full-text resolution attempt, same branch
    # app/worker/handlers.py already uses for non-research_paper types.
    genetic_variant = "genetic_variant"
    other = "other"


class ScientificScope(str, enum.Enum):
    cmt_specific = "cmt_specific"
    hereditary_neuropathy = "hereditary_neuropathy"
    peripheral_neuropathy = "peripheral_neuropathy"
    broader_neuromuscular = "broader_neuromuscular"
    general_health_relevance = "general_health_relevance"


class SourceReliability(str, enum.Enum):
    high = "high"
    moderate = "moderate"
    limited = "limited"
    unknown = "unknown"


class NewsletterStatus(str, enum.Enum):
    not_selected = "not_selected"
    selected = "selected"
    drafted = "drafted"
    under_review = "under_review"
    approved = "approved"
    scheduled = "scheduled"
    published = "published"
    rejected = "rejected"
    archived = "archived"


#: CMT Veda final-contract pass (spec item 3 -- "resolve the status
#: terminology mismatch: Veda UI uses `draft`, Discovery uses `drafted`.
#: Do not leave this to compatibility guessing.")
#:
#: RESOLUTION (documented, not guessed): `NewsletterStatus.drafted` ("drafted")
#: remains the one canonical value -- it is what `newsletter_items.status`
#: and `discovery_candidates.newsletter_status` actually store, and it is
#: not renamed by this pass (renaming a persisted state-machine value is a
#: data-rewrite / state-machine risk explicitly out of scope here, see
#: IMPLEMENTATION_STATUS.md). Veda-v1 must adopt `"drafted"` as the literal
#: it displays/compares against -- this is the one documented requirement
#: handed to Veda-v1/Lovable for this item.
#:
#: As a non-breaking courtesy (not a silent guess), `GET /candidates`'s
#: `newsletter_status` filter additionally accepts the alias key(s) below
#: and normalizes them to the canonical value before filtering -- so a
#: client that still sends `draft` keeps working. This is the ONLY place
#: the alias applies; it is never written back to storage and never
#: returned by any response (every output always uses the canonical
#: `NewsletterStatus` value). See app/api/routers/candidates.py.
NEWSLETTER_STATUS_INPUT_ALIASES: dict[str, str] = {
    "draft": NewsletterStatus.drafted.value,
}


class RagStatus(str, enum.Enum):
    not_selected = "not_selected"
    pending_approval = "pending_approval"
    approved = "approved"
    rejected = "rejected"  # spec #23 lists 8 states but #51 explicitly contrasts
    # "rejected" (human/content decision) with "failed" (technical failure),
    # so it must be a distinct, real status -- not merely a rejection_reason
    # field on some other state.
    queued = "queued"
    processing = "processing"
    indexed = "indexed"
    failed = "failed"
    removed = "removed"


class DraftStatus(str, enum.Enum):
    draft = "draft"
    in_review = "in_review"
    approved = "approved"
    superseded = "superseded"


class JobType(str, enum.Enum):
    collect_source = "collect_source"
    normalize_record = "normalize_record"
    deduplicate_record = "deduplicate_record"
    analyse_candidate = "analyse_candidate"
    resolve_full_text = "resolve_full_text"  # CMT-specific overhaul, Phase 5/6
    generate_editorial_draft = "generate_editorial_draft"
    maintenance = "maintenance"


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    dead_letter = "dead_letter"  # exceeded max attempts


class AuditAction(str, enum.Enum):
    discovered = "discovered"
    classified = "classified"
    draft_generated = "draft_generated"
    opened_for_review = "opened_for_review"
    edited = "edited"
    newsletter_selected = "newsletter_selected"
    newsletter_approved = "newsletter_approved"
    newsletter_rejected = "newsletter_rejected"
    scheduled = "scheduled"
    unscheduled = "unscheduled"  # added for CMT Veda compatibility: DELETE .../schedule (see newsletter_workflow.unschedule)
    published = "published"
    rag_submitted = "rag_submitted"
    rag_verified = "rag_verified"
    rag_approved = "rag_approved"
    rag_rejected = "rag_rejected"
    ingestion_started = "ingestion_started"
    ingestion_completed = "ingestion_completed"
    ingestion_failed = "ingestion_failed"
    removed = "removed"
    archived = "archived"
    override = "override"
    # CMT Veda final-contract pass (spec item 9 -- "persistent Newsletter
    # distribution"): written by newsletter_workflow.set_section() when a
    # NewsletterItem's section/destination is set. Deliberately distinct
    # from `edited` -- this is metadata placement, not a content edit or
    # a state-machine transition.
    section_assigned = "section_assigned"


class Role(str, enum.Enum):
    discovery_administrator = "discovery_administrator"
    reviewer = "reviewer"
    service = "service"  # for worker / internal service-to-service calls
