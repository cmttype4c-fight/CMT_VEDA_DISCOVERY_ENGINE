"""Recorded-shape fixture data for Europe PMC's REST API (core result
type), matching the documented contract app/collectors/europepmc.py and
app/services/fulltext/resolver.py parse against. Not captured from a live
call (no network access in this sandbox) -- see those modules' confidence
caveats."""

SEARCH_RESPONSE_ONE_OPEN_ACCESS_RESULT = {
    "resultList": {
        "result": [
            {
                "id": "38111111",
                "source": "MED",
                "pmid": "38111111",
                "doi": "10.1000/cmt-europepmc-example",
                "title": "Longitudinal outcomes in Charcot-Marie-Tooth disease type 1A",
                "abstractText": "We report five-year outcomes in a CMT1A cohort.",
                "journalInfo": {"journal": {"title": "Journal of Peripheral Nerve Disease"}},
                "authorList": {"author": [{"fullName": "A. Researcher"}, {"fullName": "B. Scientist"}]},
                "firstPublicationDate": "2025-03-01",
                "isOpenAccess": "Y",
                "pmcid": "PMC9999999",
                "fullTextUrlList": {
                    "fullTextUrl": [
                        {"availability": "Open access", "documentStyle": "html", "url": "https://europepmc.org/article/MED/38111111"},
                        {"availability": "Open access", "documentStyle": "pdf", "url": "https://europepmc.org/backend/ptpmcrender.fcgi?id=PMC9999999&blobtype=pdf"},
                    ]
                },
            }
        ]
    },
    "nextCursorMark": None,
}

SEARCH_RESPONSE_NOT_OPEN_ACCESS = {
    "resultList": {
        "result": [
            {
                "id": "38222222",
                "source": "MED",
                "pmid": "38222222",
                "doi": "10.1000/cmt-closed-access",
                "title": "A closed-access CMT study",
                "abstractText": "Not open access.",
                "firstPublicationDate": "2024-01-01",
                "isOpenAccess": "N",
                "fullTextUrlList": {"fullTextUrl": []},
            }
        ]
    },
    "nextCursorMark": None,
}

SEARCH_RESPONSE_EMPTY = {"resultList": {"result": []}, "nextCursorMark": None}
