"""
Full-text extraction (CMT-specific overhaul, FINAL CORRECTION #1: "the
acquired full text should be the primary source material [for Gemini
editorial generation] whenever available... do not merely store the
full-text file and then ignore it during editorial generation").

Converts the raw acquired bytes (PDF/XML/HTML -- see
app/services/fulltext/content_sniff.py for how the format is determined
from the bytes themselves, never trusted from a claim) into plain,
readable article text that app/services/intelligence/editorial_service.py
can hand to Gemini as the primary source material, with the abstract
staying as a fallback/supplement rather than the primary input.

Extraction is intentionally best-effort and never raises: a failure here
must never invalidate the candidate or the (already-successful)
full-text acquisition -- it only means editorial generation falls back to
the abstract, which is always still valid source material. Every result
records WHY extraction succeeded or failed so this is visible in
`discovery_documents.extraction_status`/`extraction_error`, never a
silent no-op.

PDF extraction requires the `pypdf` library (see requirements.txt). This
sandbox has no network access to install or verify it (same standing
constraint as every other pass -- see IMPLEMENTATION_STATUS.md), so the
PDF path is implemented against pypdf's long-stable, well-documented
`PdfReader` API but has not been exercised against a real PDF file in
this environment; if the dependency is missing entirely, extraction
fails gracefully with a clear `extraction_error` rather than crashing the
acquisition/editorial pipeline. XML and HTML extraction use only the
Python standard library (`xml.etree.ElementTree`, `html.parser`), so
those two paths have no external dependency risk at all and are
exercised directly by tests/test_fulltext_extraction.py without any
network or optional-package requirement.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

# Keep prompts a bounded, predictable size regardless of how long the
# source article is -- Gemini still gets the substantial majority of a
# typical research article/trial record's body text, without risking an
# oversized/slow request for an unusually long document. Editorial
# generation is a synthesis task, not a full-document reproduction task,
# so a generous-but-bounded excerpt is the right tradeoff.
MAX_EXTRACTED_CHARS = 20_000


@dataclass
class ExtractionResult:
    success: bool
    text: str | None = None
    char_count: int = 0
    truncated: bool = False
    error: str | None = None


def _clip(text: str) -> ExtractionResult:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return ExtractionResult(success=False, error="extraction produced no readable text")
    truncated = len(text) > MAX_EXTRACTED_CHARS
    clipped = text[:MAX_EXTRACTED_CHARS]
    return ExtractionResult(success=True, text=clipped, char_count=len(clipped), truncated=truncated)


def _extract_pdf(content: bytes) -> ExtractionResult:
    try:
        import io

        from pypdf import PdfReader
    except ImportError as exc:
        return ExtractionResult(
            success=False,
            error=f"pypdf is not installed in this environment -- cannot extract PDF text ({exc})",
        )

    try:
        reader = PdfReader(io.BytesIO(content))
        pages_text = []
        for page in reader.pages:
            page_text = page.extract_text() or ""
            if page_text:
                pages_text.append(page_text)
        return _clip("\n\n".join(pages_text))
    except Exception as exc:  # noqa: BLE001 -- any pypdf/parsing failure must degrade gracefully, never crash acquisition
        return ExtractionResult(success=False, error=f"PDF text extraction failed: {exc}")


# Tags whose contents are never meaningful article body text.
_HTML_SKIP_TAGS = {"script", "style", "nav", "header", "footer", "noscript", "svg", "form", "button", "aside"}


class _HTMLTextExtractor(HTMLParser):
    """Minimal stdlib-only HTML-to-text extractor: walks tags, skips
    non-content elements (nav/script/style/etc.), and collects the text
    of everything else. Deliberately simple -- this is meant to strip
    markup for an LLM prompt, not to preserve layout or produce
    publication-quality plain text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self.chunks: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _HTML_SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in _HTML_SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0 and data.strip():
            self.chunks.append(data.strip())


def _extract_html(content: bytes) -> ExtractionResult:
    try:
        html_text = content.decode("utf-8", errors="replace")
        parser = _HTMLTextExtractor()
        parser.feed(html_text)
        parser.close()
        return _clip("\n".join(parser.chunks))
    except Exception as exc:  # noqa: BLE001
        return ExtractionResult(success=False, error=f"HTML text extraction failed: {exc}")


def _extract_xml(content: bytes) -> ExtractionResult:
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        # Some article XML (JATS variants in particular) can be lenient
        # about entities/namespaces in ways ElementTree rejects outright.
        # Fall back to the same tag-stripping approach used for HTML --
        # still far better than discarding the document entirely.
        return _extract_html(content)

    text_parts = [t.strip() for t in root.itertext() if t and t.strip()]
    return _clip("\n".join(text_parts))


def extract_text(content: bytes, full_text_format: str | None) -> ExtractionResult:
    """Dispatches to the right extractor for an ALREADY-VALIDATED format
    (see app/services/fulltext/content_sniff.py -- callers must pass the
    format the bytes actually sniffed as, never an unverified claim).
    Unknown/unsupported formats fail cleanly rather than guessing."""
    if full_text_format == "pdf":
        return _extract_pdf(content)
    if full_text_format == "xml":
        return _extract_xml(content)
    if full_text_format == "html":
        return _extract_html(content)
    return ExtractionResult(success=False, error=f"no extractor for format {full_text_format!r}")
