"""
Recorded-shape fixture data for ClinVar's `esummary.fcgi?db=clinvar&
retmode=json` response, matching the exact field names LIVE-FETCHED and
confirmed during this pass against a real record (UID 65533 /
VCV000065533) -- see app/collectors/clinvar.py's module docstring for the
verification details. The disease/condition name and accession below are
adapted to a CMT-relevant example; the JSON *shape* (key names and
nesting) mirrors the real response exactly.
"""

ESUMMARY_RESPONSE_ONE_PATHOGENIC_VARIANT = {
    "header": {"type": "esummary", "version": "0.3"},
    "result": {
        "uids": ["77000001"],
        "77000001": {
            "uid": "77000001",
            "obj_type": "single nucleotide variant",
            "accession": "VCV000077001",
            "accession_version": "VCV000077001.3",
            "title": "NM_000530.8(PMP22):c.301C>T (p.Arg101Trp)",
            "genes": [{"symbol": "PMP22", "geneid": "5376", "strand": "+"}],
            "germline_classification": {
                "description": "Pathogenic",
                "last_evaluated": "2025/11/02 00:00",
                "review_status": "criteria provided, multiple submitters, no conflicts",
                "trait_set": [
                    {
                        "trait_name": "Charcot-Marie-Tooth disease type 1A",
                        "trait_xrefs": [{"db_source": "MONDO", "db_id": "MONDO:0007303"}],
                    }
                ],
            },
        },
    },
}

ESUMMARY_RESPONSE_UNRELATED_SOMATIC_VARIANT = {
    "header": {"type": "esummary", "version": "0.3"},
    "result": {
        "uids": ["77000002"],
        "77000002": {
            "uid": "77000002",
            "obj_type": "single nucleotide variant",
            "accession": "VCV000077002",
            "accession_version": "VCV000077002.1",
            "title": "NM_007294.4(BRCA1):c.68_69del (p.Glu23fs)",
            "genes": [{"symbol": "BRCA1", "geneid": "672", "strand": "-"}],
            # No germline_classification -- somatic-only record, the
            # graceful-fallback case parse_clinvar_summary() must handle.
            "somatic_classification": {
                "description": "Likely oncogenic",
                "trait_set": [{"trait_name": "Hereditary breast and ovarian cancer syndrome"}],
            },
        },
    },
}

ESUMMARY_RESPONSE_EMPTY = {"header": {"type": "esummary", "version": "0.3"}, "result": {"uids": []}}

ESEARCH_RESPONSE_TWO_IDS = {
    "header": {"type": "esearch", "version": "0.3"},
    "esearchresult": {"count": "2", "retmax": "2", "retstart": "0", "idlist": ["77000001", "77000002"]},
}

ESEARCH_RESPONSE_EMPTY = {
    "header": {"type": "esearch", "version": "0.3"},
    "esearchresult": {"count": "0", "retmax": "0", "retstart": "0", "idlist": []},
}
