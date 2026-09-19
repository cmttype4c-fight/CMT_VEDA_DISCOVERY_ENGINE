"""
Content-type sniffing for acquired full-text bytes (CMT-specific overhaul,
FINAL CORRECTION #3: "the system must never label an unknown or
mismatched document as PDF... do not use .pdf as a generic fallback for
unknown MIME/content").

This module inspects the ACTUAL downloaded bytes -- never trusts a
server's `Content-Type` header or the resolver's claimed
`full_text_format` alone -- and returns the genuine format, or None when
the content doesn't match any format this engine knows how to store/
extract. `app/services/fulltext/service.py` uses this as the authoritative
source of truth for `DiscoveryDocument.full_text_format`/`pdf_available`;
a resolver that claimed "pdf" but whose downloaded bytes don't start with
the PDF signature is corrected to whatever the bytes actually are (or
rejected as unsupported), never trusted at face value.

Deliberately simple, dependency-free signature checks -- not a general
MIME-sniffing library -- because the only three formats this engine ever
stores are PDF, XML, and HTML (per the acquisition priority), and a
narrow, explicit check is easier to audit and cannot be tricked by an
unrelated file that merely shares a generic byte pattern.
"""
from __future__ import annotations

import re

# The canonical PDF magic number. A PDF may have a small amount of junk
# before "%PDF-" per the spec (rare, mostly from lax generators), but
# requiring it within the first few bytes avoids false negatives from
# genuine PDFs while still being strict enough to reject non-PDF content.
_PDF_SIGNATURE = b"%PDF-"
_SNIFF_WINDOW = 4096  # bytes examined from the start of the content


def _leading_text(content: bytes) -> str:
    """Best-effort decode of the leading window, tolerant of encoding
    issues (errors="ignore") since we're only looking for ASCII markers
    like "<?xml" or "<html", not attempting a full parse."""
    return content[:_SNIFF_WINDOW].decode("utf-8", errors="ignore").lstrip("﻿").lstrip()


def sniff_format(content: bytes) -> str | None:
    """Returns "pdf", "xml", "html", or None if the content doesn't match
    any recognized format. Never guesses -- an ambiguous or unrecognized
    byte stream returns None, which the caller must treat as an
    unsupported/failed acquisition, never silently stored as if it were
    one of the three known formats."""
    if not content:
        return None

    if content[: len(_PDF_SIGNATURE)] == _PDF_SIGNATURE:
        return "pdf"

    leading = _leading_text(content)
    leading_lower = leading.lower()

    if leading_lower.startswith("<?xml"):
        # An XML document that happens to BE an XHTML/HTML document
        # (root element <html ...> in the XHTML namespace) is still
        # meaningfully "html" for our extraction purposes -- check for
        # that before defaulting to "xml".
        if re.search(r"<html[\s>]", leading_lower[:2048]):
            return "html"
        return "xml"

    if leading_lower.startswith("<!doctype html") or re.search(r"<html[\s>]", leading_lower[:2048]):
        return "html"

    # A JATS/NLM article XML sometimes has no <?xml declaration at all
    # (rare but permitted) and starts directly with <!DOCTYPE article...
    # or <article ...>. Treat a bare, well-formed-looking XML root tag as
    # "xml" only when it is NOT already identified as HTML above -- this
    # is intentionally conservative (checks for a small set of known
    # article-XML root elements rather than "starts with <") so we don't
    # misclassify arbitrary non-document content that merely starts with
    # an angle bracket.
    if re.match(r"<!doctype\s+article|<article[\s>]|<!doctype\s+html\s+public\b", leading_lower):
        if re.search(r"<html[\s>]", leading_lower[:2048]):
            return "html"
        return "xml"

    return None
