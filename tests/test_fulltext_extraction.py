"""
CMT-specific overhaul, FINAL CORRECTIVE PROMPT #1: full-text extraction
must actually produce readable text that editorial generation can use.

Unit tests for app/services/fulltext/extraction.py. XML and HTML
extraction use only the Python standard library, so those tests have no
external dependency risk. PDF extraction uses `pypdf` (see
requirements.txt) and is tested here against a REAL, generated PDF
fixture (tests/fixtures/files/sample_cmt_article.pdf) -- this is
end-to-end verified in the build sandbox (pypdf IS installed here), not
a guess: see IMPLEMENTATION_STATUS.md for exactly how that fixture was
produced and confirmed to extract correctly before being checked in.
"""
import os

from app.services.fulltext.extraction import extract_text

_FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "files")
_SAMPLE_PDF_PATH = os.path.join(_FIXTURE_DIR, "sample_cmt_article.pdf")


def test_extract_html_strips_script_style_and_nav():
    html = (
        b"<html><head><script>alert('evil')</script><style>.x{color:red}</style></head>"
        b"<body><nav>Home | About</nav><h1>Natural history of CMT1A</h1>"
        b"<p>A longitudinal study of disease progression.</p>"
        b"<footer>Copyright 2026</footer></body></html>"
    )
    result = extract_text(html, "html")
    assert result.success is True
    assert "Natural history of CMT1A" in result.text
    assert "longitudinal study" in result.text
    assert "alert" not in result.text
    assert "color:red" not in result.text
    assert "Home | About" not in result.text
    assert "Copyright" not in result.text


def test_extract_xml_pulls_text_nodes():
    xml = (
        b'<?xml version="1.0"?><article><front><title-group>'
        b"<article-title>A GDAP1 variant in Charcot-Marie-Tooth disease</article-title>"
        b"</title-group></front><body><p>Genetic analysis identified a novel variant.</p></body></article>"
    )
    result = extract_text(xml, "xml")
    assert result.success is True
    assert "GDAP1" in result.text
    assert "Genetic analysis identified" in result.text


def test_extract_malformed_xml_falls_back_to_tag_stripping():
    """Some real-world article XML is lenient in ways ElementTree rejects
    (bad entities, etc.) -- extraction must still recover readable text
    rather than failing outright."""
    malformed = b"<article><p>Text with an unescaped ampersand & no closing tag"
    result = extract_text(malformed, "xml")
    # Falls back to the HTML-style tag stripper; still produces text.
    assert result.success is True
    assert "Text with an unescaped ampersand" in result.text


def test_extract_pdf_against_real_generated_fixture():
    """End-to-end verified against a real PDF file (not a mocked/guessed
    byte string) -- see the module docstring above."""
    assert os.path.exists(_SAMPLE_PDF_PATH), (
        "test fixture missing -- see IMPLEMENTATION_STATUS.md for how "
        "tests/fixtures/files/sample_cmt_article.pdf was generated"
    )
    with open(_SAMPLE_PDF_PATH, "rb") as fh:
        content = fh.read()

    result = extract_text(content, "pdf")
    assert result.success is True
    assert result.char_count > 0
    assert "Charcot-Marie-Tooth disease type 1A" in result.text
    assert "PMP22 duplication" in result.text


def test_extract_pdf_garbage_bytes_fails_gracefully():
    """Content that sniffed as 'pdf' (or was otherwise mis-routed here)
    but isn't actually a parseable PDF must fail cleanly with a
    diagnostic error, never raise."""
    result = extract_text(b"not a real pdf file", "pdf")
    assert result.success is False
    assert result.error is not None
    assert result.text is None


def test_extract_unknown_format_fails_cleanly():
    result = extract_text(b"whatever", "other")
    assert result.success is False
    assert "no extractor" in result.error


def test_extract_truncates_very_long_text():
    from app.services.fulltext.extraction import MAX_EXTRACTED_CHARS

    long_html = b"<html><body><p>" + (b"word " * 10000) + b"</p></body></html>"
    result = extract_text(long_html, "html")
    assert result.success is True
    assert result.truncated is True
    assert result.char_count <= MAX_EXTRACTED_CHARS


def test_extract_empty_document_is_not_a_success():
    result = extract_text(b"<html><body></body></html>", "html")
    assert result.success is False
    assert result.text is None
