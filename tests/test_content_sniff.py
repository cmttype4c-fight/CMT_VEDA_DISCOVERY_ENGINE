"""
CMT-specific overhaul, FINAL CORRECTIVE PROMPT #3: "the system must never
label an unknown or mismatched document as PDF... do not use .pdf as a
generic fallback for unknown MIME/content."

Pure unit tests for app/services/fulltext/content_sniff.py -- no network,
no DB, no external dependency. These are the ground-truth signature
checks that app/services/fulltext/service.py trusts over any claimed
format from the resolver or a server's Content-Type header.
"""
from app.services.fulltext.content_sniff import sniff_format


def test_sniffs_genuine_pdf_signature():
    assert sniff_format(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj") == "pdf"


def test_sniffs_xml_declaration():
    assert sniff_format(b'<?xml version="1.0" encoding="UTF-8"?><article><title>x</title></article>') == "xml"


def test_sniffs_jats_article_without_xml_declaration():
    """Some article XML omits the <?xml ...?> declaration entirely but
    starts directly with a DOCTYPE or root element -- still valid XML
    that must be recognized, not rejected as unknown."""
    assert sniff_format(b"<!DOCTYPE article PUBLIC \"-//NLM//DTD JATS\"><article><body/></article>") == "xml"
    assert sniff_format(b"<article><body>text</body></article>") == "xml"


def test_sniffs_html_doctype():
    assert sniff_format(b"<!DOCTYPE html><html><body>hi</body></html>") == "html"


def test_sniffs_bare_html_tag_without_doctype():
    assert sniff_format(b"<html><head></head><body>hi</body></html>") == "html"


def test_xhtml_with_xml_declaration_is_still_classified_html():
    """An XHTML document has both an <?xml ...?> declaration AND an
    <html> root -- for extraction purposes this should be treated as
    HTML (the html.parser-based extractor), not generic XML."""
    xhtml = b'<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body>x</body></html>'
    assert sniff_format(xhtml) == "html"


def test_unrecognized_binary_content_is_none():
    """FINAL CORRECTIVE PROMPT #3's core requirement: unknown/unsupported
    content must never be labeled as any of the three known formats."""
    assert sniff_format(b"\x89PNG\r\n\x1a\nnot a document at all, just random binary junk") is None
    assert sniff_format(b"plain text that is not markup of any kind") is None


def test_empty_content_is_none():
    assert sniff_format(b"") is None


def test_html_with_leading_whitespace_and_bom_is_still_detected():
    content = b"\xef\xbb\xbf   \n<!DOCTYPE html><html><body>hi</body></html>"
    assert sniff_format(content) == "html"


def test_pdf_signature_must_be_at_the_very_start():
    """A PDF signature appearing later in a stream (e.g. embedded inside
    an HTML page that merely mentions "%PDF-" in its text) must not be
    mistaken for an actual PDF file."""
    content = b"<html><body>This page links to a file starting with %PDF-1.4 in its name</body></html>"
    assert sniff_format(content) == "html"
