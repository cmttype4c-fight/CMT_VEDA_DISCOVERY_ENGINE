"""
Recorded-shape fixture data for the WordPress REST API's
`/wp-json/wp/v2/posts` endpoint, matching the exact field names
LIVE-FETCHED and confirmed during this pass against real CMT ecosystem
organization sites (CMTRF, ECMTF, CMT Australia, Peripheral Nerve
Society) -- see app/collectors/wordpress_json.py's module docstring. The
post titles/URLs below are adapted examples; the JSON *shape* (key names
and nesting, including the `{"rendered": ...}` wrapper WordPress core uses
for every HTML field) mirrors the real response exactly.
"""

POSTS_RESPONSE_ONE_POST = [
    {
        "id": 43999,
        "date": "2026-09-01T09:00:00",
        "date_gmt": "2026-09-01T09:00:00",
        "link": "https://example-cmt-org.org/2026/09/01/research-update/",
        "title": {"rendered": "Research Update: New CMT1A Natural History Study"},
        "excerpt": {"rendered": "<p>A summary of findings from our latest natural history study.</p>\n"},
        "content": {
            "rendered": "<p>A summary of findings from our latest natural history study. "
            "Full details are available in the linked report.</p>\n"
        },
    }
]

POSTS_RESPONSE_EMPTY: list = []

POSTS_RESPONSE_MISSING_LINK = [
    {
        "id": 44000,
        "date": "2026-09-02T09:00:00",
        "title": {"rendered": "Malformed entry with no link"},
        # no "link" key -- must be skipped, not raise
    }
]
