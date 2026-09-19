"""
CMT-specific overhaul, Phase 5/6: full-text resolution, acquisition,
storage, dedup, and provenance.

Covers required test list items:
  14. Accessible PDF is resolved.
  15. Inaccessible/paywalled paper is handled without failure of the candidate.
  16. PDF hash prevents duplicate storage.
  17. Full-text acquisition is retryable/idempotent.
  18. Provenance is retained.
"""
import httpx
import respx

from app.models.candidate import DiscoveryCandidate
from app.services.fulltext.resolver import _pick_best_url, resolve_full_text
from app.services.fulltext.service import acquire_full_text
from app.services.fulltext.storage import LocalDiskDocumentStorage
from tests.fixtures.europepmc_responses import SEARCH_RESPONSE_NOT_OPEN_ACCESS, SEARCH_RESPONSE_ONE_OPEN_ACCESS_RESULT


# --- _pick_best_url format priority (unit-level, no network) ---

def test_pick_best_url_prefers_pdf_when_all_three_present():
    urls = [
        {"documentStyle": "html", "url": "https://example.org/a.html"},
        {"documentStyle": "xml", "url": "https://example.org/a.xml"},
        {"documentStyle": "pdf", "url": "https://example.org/a.pdf"},
    ]
    url, mime, fmt = _pick_best_url(urls)
    assert (url, mime, fmt) == ("https://example.org/a.pdf", "application/pdf", "pdf")


def test_pick_best_url_falls_back_to_xml_without_pdf():
    urls = [
        {"documentStyle": "html", "url": "https://example.org/a.html"},
        {"documentStyle": "xml", "url": "https://example.org/a.xml"},
    ]
    url, mime, fmt = _pick_best_url(urls)
    assert (url, mime, fmt) == ("https://example.org/a.xml", "application/xml", "xml")


def test_pick_best_url_falls_back_to_html_without_pdf_or_xml():
    urls = [{"documentStyle": "html", "url": "https://example.org/a.html"}]
    url, mime, fmt = _pick_best_url(urls)
    assert (url, mime, fmt) == ("https://example.org/a.html", "text/html", "html")


def test_pick_best_url_never_mislabels_html_as_pdf():
    """Corrective prompt #5: 'do not pretend that HTML or XML is a PDF.'"""
    urls = [{"documentStyle": "html", "url": "https://example.org/a.html"}]
    _, mime, fmt = _pick_best_url(urls)
    assert fmt != "pdf"
    assert mime != "application/pdf"


def test_pick_best_url_returns_nothing_for_empty_list():
    assert _pick_best_url([]) == (None, None, None)


def _candidate(**overrides):
    from datetime import datetime, timezone

    defaults = dict(
        content_type="research_paper", title="A CMT paper", discovered_at=datetime.now(timezone.utc),
        pmid="38111111", doi="10.1000/cmt-europepmc-example",
    )
    defaults.update(overrides)
    return DiscoveryCandidate(**defaults)


# --- storage ---

def test_local_disk_storage_dedups_identical_content(tmp_path):
    storage = LocalDiskDocumentStorage(base_dir=str(tmp_path))
    content = b"%PDF-1.4 fake pdf content"

    first = storage.save(content)
    second = storage.save(content)

    assert first.content_hash == second.content_hash
    assert first.document_ref == second.document_ref
    assert storage.exists(first.content_hash) is True


def test_local_disk_storage_different_content_different_ref(tmp_path):
    storage = LocalDiskDocumentStorage(base_dir=str(tmp_path))
    a = storage.save(b"content A")
    b = storage.save(b"content B")
    assert a.content_hash != b.content_hash
    assert a.document_ref != b.document_ref


# --- resolver (test list item 14/15) ---

@respx.mock
async def test_resolve_full_text_finds_open_access_pdf():
    respx.route(method="GET", host="www.ebi.ac.uk").mock(
        return_value=httpx.Response(200, json=SEARCH_RESPONSE_ONE_OPEN_ACCESS_RESULT)
    )
    candidate = _candidate()
    result = await resolve_full_text(candidate)
    assert result.available is True
    assert result.url is not None
    assert result.mime_type == "application/pdf"
    assert result.full_text_format == "pdf"


@respx.mock
async def test_resolve_full_text_handles_closed_access_without_error():
    """Test list item 15: inaccessible/paywalled paper is handled without
    failure -- resolve_full_text must return available=False, not raise."""
    respx.route(method="GET", host="www.ebi.ac.uk").mock(
        return_value=httpx.Response(200, json=SEARCH_RESPONSE_NOT_OPEN_ACCESS)
    )
    candidate = _candidate(pmid="38222222", doi="10.1000/cmt-closed-access")
    result = await resolve_full_text(candidate)
    assert result.available is False
    assert result.reason is not None


async def test_resolve_full_text_with_no_doi_or_pmid_is_unavailable_not_an_error():
    candidate = _candidate(pmid=None, doi=None)
    result = await resolve_full_text(candidate)
    assert result.available is False


# --- full end-to-end acquisition (test list items 14-18) ---

@respx.mock
async def test_acquire_full_text_happy_path_sets_candidate_flag_and_provenance(db_session, monkeypatch, tmp_path):
    from app.services.fulltext import service as service_module
    from app.services.fulltext.storage import LocalDiskDocumentStorage as _LocalStorage

    monkeypatch.setattr(service_module, "get_document_storage", lambda: _LocalStorage(str(tmp_path)))

    respx.route(method="GET", host="www.ebi.ac.uk").mock(
        return_value=httpx.Response(200, json=SEARCH_RESPONSE_ONE_OPEN_ACCESS_RESULT)
    )
    respx.route(method="GET", host="europepmc.org").mock(
        return_value=httpx.Response(200, content=b"%PDF-1.4 fake pdf bytes", headers={"content-type": "application/pdf"})
    )

    candidate = _candidate()
    db_session.add(candidate)
    db_session.flush()

    doc = await acquire_full_text(db_session, candidate)

    assert doc.retrieval_status == "acquired"
    assert doc.content_hash is not None
    assert doc.document_ref is not None
    assert doc.license_provenance is not None  # test list item 18: provenance retained
    assert candidate.full_text_available is True


@respx.mock
async def test_acquire_full_text_unavailable_does_not_invalidate_candidate(db_session):
    """Test list item 15, at the orchestration level: candidate must
    remain a perfectly valid candidate when full text can't be found."""
    respx.route(method="GET", host="www.ebi.ac.uk").mock(
        return_value=httpx.Response(200, json=SEARCH_RESPONSE_NOT_OPEN_ACCESS)
    )
    candidate = _candidate(pmid="38222222", doi="10.1000/cmt-closed-access")
    db_session.add(candidate)
    db_session.flush()

    doc = await acquire_full_text(db_session, candidate)

    assert doc.retrieval_status == "unavailable"
    assert candidate.full_text_available is False
    # Candidate itself is completely untouched/valid otherwise.
    assert candidate.title == "A CMT paper"


@respx.mock
async def test_acquire_full_text_is_idempotent_on_retry(db_session, monkeypatch, tmp_path):
    """Test list item 17: retryable/idempotent -- calling acquire_full_text
    again after a successful acquisition short-circuits to the existing row
    rather than re-downloading."""
    from app.services.fulltext import service as service_module
    from app.services.fulltext.storage import LocalDiskDocumentStorage as _LocalStorage

    monkeypatch.setattr(service_module, "get_document_storage", lambda: _LocalStorage(str(tmp_path)))

    call_count = {"n": 0}

    def _search_response(request):
        call_count["n"] += 1
        return httpx.Response(200, json=SEARCH_RESPONSE_ONE_OPEN_ACCESS_RESULT)

    respx.route(method="GET", host="www.ebi.ac.uk").mock(side_effect=_search_response)
    respx.route(method="GET", host="europepmc.org").mock(
        return_value=httpx.Response(200, content=b"%PDF-1.4 fake pdf bytes", headers={"content-type": "application/pdf"})
    )

    candidate = _candidate()
    db_session.add(candidate)
    db_session.flush()

    first = await acquire_full_text(db_session, candidate)
    second = await acquire_full_text(db_session, candidate)

    assert first.id == second.id
    assert call_count["n"] == 1  # second call short-circuited, never re-resolved


@respx.mock
async def test_acquire_full_text_hash_dedups_storage_but_keeps_per_candidate_rows(db_session, tmp_path, monkeypatch):
    """Test list item 16 (fixed per FINAL CORRECTIVE PROMPT #10): PDF hash
    prevents *duplicate physical storage* -- two different candidates
    resolving to the identical physical file (e.g. a preprint and its
    published version) share the same file on disk (content_hash,
    document_ref) -- but each candidate MUST still get its OWN
    discovery_documents row, not be handed the other candidate's row.
    Before the fix, `acquire_full_text` for candidate_b would return
    candidate_a's row verbatim (same `id`, `candidate_id == candidate_a.id`),
    silently losing candidate_b's own document association -- this test
    asserts that no longer happens."""
    from app.services.fulltext import service as service_module
    from app.services.fulltext.storage import LocalDiskDocumentStorage as _LocalStorage

    monkeypatch.setattr(service_module, "get_document_storage", lambda: _LocalStorage(str(tmp_path)))

    respx.route(method="GET", host="www.ebi.ac.uk").mock(
        return_value=httpx.Response(200, json=SEARCH_RESPONSE_ONE_OPEN_ACCESS_RESULT)
    )
    respx.route(method="GET", host="europepmc.org").mock(
        return_value=httpx.Response(200, content=b"%PDF-1.4 identical bytes", headers={"content-type": "application/pdf"})
    )

    candidate_a = _candidate(pmid="38111111", doi="10.1000/cmt-europepmc-example")
    candidate_b = _candidate(pmid="38111111", doi="10.1000/cmt-europepmc-example", title="Duplicate physical file")
    db_session.add_all([candidate_a, candidate_b])
    db_session.flush()

    doc_a = await acquire_full_text(db_session, candidate_a)
    doc_b = await acquire_full_text(db_session, candidate_b)

    # Physical storage is deduped (same bytes -> same hash/ref)...
    assert doc_a.content_hash == doc_b.content_hash
    assert doc_a.document_ref == doc_b.document_ref
    # ...but each candidate has ITS OWN row, correctly associated.
    assert doc_a.id != doc_b.id
    assert doc_a.candidate_id == candidate_a.id
    assert doc_b.candidate_id == candidate_b.id
    assert candidate_a.full_text_available is True
    assert candidate_b.full_text_available is True

    # And each candidate can look up ITS OWN document by its own id.
    from sqlalchemy import select

    from app.models.document import DiscoveryDocument

    found_for_a = db_session.execute(
        select(DiscoveryDocument).where(DiscoveryDocument.candidate_id == candidate_a.id)
    ).scalars().all()
    found_for_b = db_session.execute(
        select(DiscoveryDocument).where(DiscoveryDocument.candidate_id == candidate_b.id)
    ).scalars().all()
    assert len(found_for_a) == 1 and found_for_a[0].id == doc_a.id
    assert len(found_for_b) == 1 and found_for_b[0].id == doc_b.id


@respx.mock
async def test_acquire_full_text_prefers_pdf_over_xml_and_html(db_session, tmp_path, monkeypatch):
    """Test list item 14 (format priority, corrective prompt #5): when a
    fullTextUrlList offers pdf, xml, AND html, the pdf variant is chosen
    and full_text_format/pdf_available are set accurately."""
    from app.services.fulltext import service as service_module
    from app.services.fulltext.storage import LocalDiskDocumentStorage as _LocalStorage

    monkeypatch.setattr(service_module, "get_document_storage", lambda: _LocalStorage(str(tmp_path)))

    multi_format_response = {
        "resultList": {
            "result": [
                {
                    "id": "38333333", "source": "MED", "pmid": "38333333",
                    "doi": "10.1000/cmt-multi-format",
                    "title": "A CMT paper with multiple full-text formats available",
                    "isOpenAccess": "Y",
                    "fullTextUrlList": {
                        "fullTextUrl": [
                            {"availability": "Open access", "documentStyle": "html", "url": "https://europepmc.org/article/MED/38333333"},
                            {"availability": "Open access", "documentStyle": "xml", "url": "https://europepmc.org/backend/fulltext.xml?id=38333333"},
                            {"availability": "Open access", "documentStyle": "pdf", "url": "https://europepmc.org/backend/ptpmcrender.fcgi?id=38333333&blobtype=pdf"},
                        ]
                    },
                }
            ]
        },
        "nextCursorMark": None,
    }
    respx.route(method="GET", host="www.ebi.ac.uk").mock(return_value=httpx.Response(200, json=multi_format_response))
    respx.route(method="GET", host="europepmc.org").mock(
        return_value=httpx.Response(200, content=b"%PDF-1.4 the pdf variant", headers={"content-type": "application/pdf"})
    )

    candidate = _candidate(pmid="38333333", doi="10.1000/cmt-multi-format")
    db_session.add(candidate)
    db_session.flush()

    doc = await acquire_full_text(db_session, candidate)

    assert doc.full_text_format == "pdf"
    assert doc.pdf_available is True


@respx.mock
async def test_acquire_full_text_falls_back_to_xml_when_no_pdf(db_session, tmp_path, monkeypatch):
    """Test list item 14/15: PDF unavailable + XML available -> XML is
    selected, and pdf_available is explicitly False while
    full_text_available (via the candidate flag) remains True -- a
    missing PDF must not invalidate the candidate."""
    from app.services.fulltext import service as service_module
    from app.services.fulltext.storage import LocalDiskDocumentStorage as _LocalStorage

    monkeypatch.setattr(service_module, "get_document_storage", lambda: _LocalStorage(str(tmp_path)))

    xml_only_response = {
        "resultList": {
            "result": [
                {
                    "id": "38444444", "source": "MED", "pmid": "38444444",
                    "doi": "10.1000/cmt-xml-only",
                    "title": "A CMT paper with only XML full text available",
                    "isOpenAccess": "Y",
                    "fullTextUrlList": {
                        "fullTextUrl": [
                            {"availability": "Open access", "documentStyle": "xml", "url": "https://europepmc.org/backend/fulltext.xml?id=38444444"},
                        ]
                    },
                }
            ]
        },
        "nextCursorMark": None,
    }
    respx.route(method="GET", host="www.ebi.ac.uk").mock(return_value=httpx.Response(200, json=xml_only_response))
    respx.route(method="GET", host="europepmc.org").mock(
        return_value=httpx.Response(200, content=b"<article>xml full text</article>", headers={"content-type": "application/xml"})
    )

    candidate = _candidate(pmid="38444444", doi="10.1000/cmt-xml-only")
    db_session.add(candidate)
    db_session.flush()

    doc = await acquire_full_text(db_session, candidate)

    assert doc.full_text_format == "xml"
    assert doc.pdf_available is False
    assert candidate.full_text_available is True  # XML full text still counts as available


@respx.mock
async def test_acquire_full_text_falls_back_to_html_when_no_pdf_or_xml(db_session, tmp_path, monkeypatch):
    """Test list item 14/15: PDF/XML unavailable + HTML full text
    available -> HTML is selected and recorded accurately (never
    mislabeled as a PDF)."""
    from app.services.fulltext import service as service_module
    from app.services.fulltext.storage import LocalDiskDocumentStorage as _LocalStorage

    monkeypatch.setattr(service_module, "get_document_storage", lambda: _LocalStorage(str(tmp_path)))

    html_only_response = {
        "resultList": {
            "result": [
                {
                    "id": "38555555", "source": "MED", "pmid": "38555555",
                    "doi": "10.1000/cmt-html-only",
                    "title": "A CMT paper with only HTML full text available",
                    "isOpenAccess": "Y",
                    "fullTextUrlList": {
                        "fullTextUrl": [
                            {"availability": "Open access", "documentStyle": "html", "url": "https://europepmc.org/article/MED/38555555"},
                        ]
                    },
                }
            ]
        },
        "nextCursorMark": None,
    }
    respx.route(method="GET", host="www.ebi.ac.uk").mock(return_value=httpx.Response(200, json=html_only_response))
    respx.route(method="GET", host="europepmc.org").mock(
        return_value=httpx.Response(200, content=b"<html>full text</html>", headers={"content-type": "text/html"})
    )

    candidate = _candidate(pmid="38555555", doi="10.1000/cmt-html-only")
    db_session.add(candidate)
    db_session.flush()

    doc = await acquire_full_text(db_session, candidate)

    assert doc.full_text_format == "html"
    assert doc.pdf_available is False
    assert candidate.full_text_available is True


# --- content-type validation (FINAL CORRECTIVE PROMPT #3) ---

@respx.mock
async def test_acquire_full_text_rejects_unrecognized_content_as_unsupported(db_session, tmp_path, monkeypatch):
    """The resolver claims a PDF is available, but the actual downloaded
    bytes don't match any known format (e.g. an error page, a redirect
    target that wasn't really the PDF). Must NOT be stored or labeled as
    a PDF -- retrieval_status becomes "unsupported", full_text_format
    stays None, and the candidate's full_text_available stays False."""
    from app.services.fulltext import service as service_module
    from app.services.fulltext.storage import LocalDiskDocumentStorage as _LocalStorage

    monkeypatch.setattr(service_module, "get_document_storage", lambda: _LocalStorage(str(tmp_path)))

    respx.route(method="GET", host="www.ebi.ac.uk").mock(
        return_value=httpx.Response(200, json=SEARCH_RESPONSE_ONE_OPEN_ACCESS_RESULT)
    )
    # Claims to be a PDF (per the fixture's documentStyle="pdf" entry) but
    # the actual response body is neither PDF, XML, nor HTML.
    respx.route(method="GET", host="europepmc.org").mock(
        return_value=httpx.Response(200, content=b"Service Temporarily Unavailable - not a real document", headers={"content-type": "application/pdf"})
    )

    candidate = _candidate()
    db_session.add(candidate)
    db_session.flush()

    doc = await acquire_full_text(db_session, candidate)

    assert doc.retrieval_status == "unsupported"
    assert doc.full_text_format is None
    assert doc.pdf_available is False
    assert doc.document_ref is None  # never stored
    assert doc.content_hash is None
    assert candidate.full_text_available is False
    assert "not stored" in doc.error_detail or "did not match" in doc.error_detail


@respx.mock
async def test_acquire_full_text_corrects_mislabeled_format(db_session, tmp_path, monkeypatch):
    """The resolver's metadata claims 'pdf' (documentStyle says so), but
    the actual downloaded bytes are genuinely HTML. The engine must
    record the format it actually verified (html), never trust the
    claim -- this is the literal 'mismatched document' case from the
    corrective prompt, distinct from a wholly unrecognized one."""
    from app.services.fulltext import service as service_module
    from app.services.fulltext.storage import LocalDiskDocumentStorage as _LocalStorage

    monkeypatch.setattr(service_module, "get_document_storage", lambda: _LocalStorage(str(tmp_path)))

    respx.route(method="GET", host="www.ebi.ac.uk").mock(
        return_value=httpx.Response(200, json=SEARCH_RESPONSE_ONE_OPEN_ACCESS_RESULT)
    )
    # Claimed content-type header says PDF, but the body is actually HTML.
    respx.route(method="GET", host="europepmc.org").mock(
        return_value=httpx.Response(
            200, content=b"<html><body><p>Actually an HTML error/landing page.</p></body></html>",
            headers={"content-type": "application/pdf"},
        )
    )

    candidate = _candidate()
    db_session.add(candidate)
    db_session.flush()

    doc = await acquire_full_text(db_session, candidate)

    assert doc.retrieval_status == "acquired"
    assert doc.full_text_format == "html"  # corrected, never "pdf"
    assert doc.pdf_available is False
    assert doc.extraction_status == "success"
    assert "Actually an HTML error" in doc.extracted_text


# --- EUROPE PMC FULL-TEXT CORRECTION: PDF -> official XML fallback ---

_PMC_XML_BODY = (
    b"<?xml version='1.0' encoding='UTF-8'?>"
    b"<article><body><p>Real full-text XML content for a CMT paper, standing in for "
    b"what the official Europe PMC fullTextXML endpoint actually returned during this "
    b"pass's VPS testing against four live PMCIDs.</p></body></article>"
)

_PDF_403_AND_PMCID_RESPONSE = {
    "resultList": {
        "result": [
            {
                "id": "39000001", "source": "MED", "pmid": "39000001",
                "doi": "10.1000/cmt-pdf-blocked", "title": "A CMT paper whose PDF render URL is blocked",
                "isOpenAccess": "Y", "pmcid": "PMC13571996",
                "fullTextUrlList": {
                    "fullTextUrl": [
                        {"documentStyle": "pdf", "url": "https://europepmc.org/articles/PMC13571996?pdf=render"},
                        {"documentStyle": "html", "url": "https://europepmc.org/article/MED/39000001"},
                    ]
                },
            }
        ]
    },
    "nextCursorMark": None,
}


@respx.mock
async def test_resolve_full_text_offers_official_fulltextxml_endpoint_even_without_xml_style(db_session):
    """The core of this correction: the official
    https://www.ebi.ac.uk/europepmc/webservices/rest/{PMCID}/fullTextXML
    endpoint must be offered as the XML candidate purely from the
    result's `pmcid`, even though this fixture's own `fullTextUrlList`
    lists no 'xml' documentStyle entry at all."""
    respx.route(method="GET", host="www.ebi.ac.uk").mock(
        return_value=httpx.Response(200, json=_PDF_403_AND_PMCID_RESPONSE)
    )
    candidate = _candidate(pmid="39000001", doi="10.1000/cmt-pdf-blocked")
    result = await resolve_full_text(candidate)
    assert result.available is True
    formats = [c.full_text_format for c in result.candidates]
    assert formats == ["pdf", "xml", "html"]
    xml_candidate = result.candidates[1]
    assert xml_candidate.url == "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC13571996/fullTextXML"
    assert xml_candidate.full_text_source == "europepmc_fulltextxml_api"


@respx.mock
async def test_pdf_403_falls_through_to_official_fulltextxml_and_succeeds(db_session, tmp_path, monkeypatch):
    """THE regression test this correction explicitly requires: 'PDF
    unavailable/403 -> official Europe PMC fullTextXML -> successful
    document + extracted text.' Reproduces exactly what real VPS testing
    found this pass: the PDF render URL 403s, the official fullTextXML
    endpoint (keyed by PMCID) returns 200 with real article XML -- and
    this must NOT be a terminal candidate failure."""
    from app.services.fulltext import service as service_module
    from app.services.fulltext.storage import LocalDiskDocumentStorage as _LocalStorage

    monkeypatch.setattr(service_module, "get_document_storage", lambda: _LocalStorage(str(tmp_path)))

    respx.route(method="GET", host="www.ebi.ac.uk", path="/europepmc/webservices/rest/search").mock(
        return_value=httpx.Response(200, json=_PDF_403_AND_PMCID_RESPONSE)
    )
    respx.route(method="GET", url="https://europepmc.org/articles/PMC13571996?pdf=render").mock(
        return_value=httpx.Response(403, text="Forbidden")
    )
    respx.route(
        method="GET",
        url="https://www.ebi.ac.uk/europepmc/webservices/rest/PMC13571996/fullTextXML",
    ).mock(return_value=httpx.Response(200, content=_PMC_XML_BODY, headers={"content-type": "application/xml"}))

    candidate = _candidate(pmid="39000001", doi="10.1000/cmt-pdf-blocked")
    db_session.add(candidate)
    db_session.flush()

    doc = await acquire_full_text(db_session, candidate)

    assert doc.retrieval_status == "acquired"
    assert doc.full_text_format == "xml"  # stored format: pdf -> pdf, xml -> xml, html -> html
    assert doc.pdf_available is False  # must remain False when XML is used
    assert doc.full_text_source == "europepmc_fulltextxml_api"
    assert doc.document_url == "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC13571996/fullTextXML"
    assert doc.extraction_status == "success"
    assert doc.extracted_text is not None
    assert "Real full-text XML content" in doc.extracted_text
    assert doc.extracted_char_count == len(doc.extracted_text)
    assert candidate.full_text_available is True  # must become True from valid XML, never abstract-only


@respx.mock
async def test_pdf_success_does_not_fall_through_to_xml_or_html(db_session, tmp_path, monkeypatch):
    """Sanity check in the other direction: when the PDF genuinely
    downloads successfully, the XML/HTML candidates are never even
    attempted -- PDF stays the top priority when it is truly accessible."""
    from app.services.fulltext import service as service_module
    from app.services.fulltext.storage import LocalDiskDocumentStorage as _LocalStorage

    monkeypatch.setattr(service_module, "get_document_storage", lambda: _LocalStorage(str(tmp_path)))

    respx.route(method="GET", host="www.ebi.ac.uk", path="/europepmc/webservices/rest/search").mock(
        return_value=httpx.Response(200, json=_PDF_403_AND_PMCID_RESPONSE)
    )
    pdf_route = respx.route(method="GET", url="https://europepmc.org/articles/PMC13571996?pdf=render").mock(
        return_value=httpx.Response(200, content=b"%PDF-1.4 genuine pdf bytes", headers={"content-type": "application/pdf"})
    )
    xml_route = respx.route(
        method="GET", url="https://www.ebi.ac.uk/europepmc/webservices/rest/PMC13571996/fullTextXML"
    ).mock(return_value=httpx.Response(200, content=_PMC_XML_BODY))

    candidate = _candidate(pmid="39000001", doi="10.1000/cmt-pdf-blocked")
    db_session.add(candidate)
    db_session.flush()

    doc = await acquire_full_text(db_session, candidate)

    assert doc.full_text_format == "pdf"
    assert doc.pdf_available is True
    assert xml_route.call_count == 0  # never attempted -- PDF succeeded first


@respx.mock
async def test_both_pdf_and_xml_fail_falls_through_to_html(db_session, tmp_path, monkeypatch):
    """Full three-deep fallback: PDF 403s, the official XML endpoint also
    fails, HTML is the last legitimate fallback and is used."""
    from app.services.fulltext import service as service_module
    from app.services.fulltext.storage import LocalDiskDocumentStorage as _LocalStorage

    monkeypatch.setattr(service_module, "get_document_storage", lambda: _LocalStorage(str(tmp_path)))

    respx.route(method="GET", host="www.ebi.ac.uk", path="/europepmc/webservices/rest/search").mock(
        return_value=httpx.Response(200, json=_PDF_403_AND_PMCID_RESPONSE)
    )
    respx.route(method="GET", url="https://europepmc.org/articles/PMC13571996?pdf=render").mock(
        return_value=httpx.Response(403, text="Forbidden")
    )
    respx.route(
        method="GET", url="https://www.ebi.ac.uk/europepmc/webservices/rest/PMC13571996/fullTextXML"
    ).mock(return_value=httpx.Response(404, text="Not Found"))
    respx.route(method="GET", url="https://europepmc.org/article/MED/39000001").mock(
        return_value=httpx.Response(200, content=b"<html><body>full text</body></html>", headers={"content-type": "text/html"})
    )

    candidate = _candidate(pmid="39000001", doi="10.1000/cmt-pdf-blocked")
    db_session.add(candidate)
    db_session.flush()

    doc = await acquire_full_text(db_session, candidate)

    assert doc.retrieval_status == "acquired"
    assert doc.full_text_format == "html"
    assert doc.pdf_available is False
    assert candidate.full_text_available is True


@respx.mock
async def test_acquire_full_text_populates_extraction_fields(db_session, tmp_path, monkeypatch):
    """FINAL CORRECTIVE PROMPT #1: acquisition must actually extract
    readable text, not just store the raw file."""
    from app.services.fulltext import service as service_module
    from app.services.fulltext.storage import LocalDiskDocumentStorage as _LocalStorage

    monkeypatch.setattr(service_module, "get_document_storage", lambda: _LocalStorage(str(tmp_path)))

    respx.route(method="GET", host="www.ebi.ac.uk").mock(
        return_value=httpx.Response(200, json=SEARCH_RESPONSE_ONE_OPEN_ACCESS_RESULT)
    )
    respx.route(method="GET", host="europepmc.org").mock(
        return_value=httpx.Response(
            200, content=b"<html><body><h1>CMT1A cohort</h1><p>Findings about disease progression.</p></body></html>",
            headers={"content-type": "text/html"},
        )
    )

    candidate = _candidate()
    db_session.add(candidate)
    db_session.flush()

    doc = await acquire_full_text(db_session, candidate)

    assert doc.extraction_status == "success"
    assert doc.extracted_text is not None
    assert "CMT1A cohort" in doc.extracted_text
    assert "Findings about disease progression" in doc.extracted_text
    assert doc.extracted_char_count == len(doc.extracted_text)
