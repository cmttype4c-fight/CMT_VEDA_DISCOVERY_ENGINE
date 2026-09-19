"""Recorded-shape (hand-built, not live) ClinicalTrials.gov API v2 JSON
fixture, matching the real /studies response schema closely enough to
exercise app.collectors.clinicaltrials.parse_clinicaltrials_study() with
zero network access (spec #45, #48)."""

SAMPLE_STUDIES_RESPONSE = {
    "studies": [
        {
            "protocolSection": {
                "identificationModule": {
                    "nctId": "NCT90000099",
                    "briefTitle": "Study of Nerve Conduction Monitoring in CMT1A",
                    "officialTitle": "A Longitudinal Study of Nerve Conduction Monitoring in Charcot-Marie-Tooth Disease Type 1A",
                },
                "statusModule": {
                    "overallStatus": "RECRUITING",
                    "startDateStruct": {"date": "2025-02-01"},
                    "lastUpdatePostDateStruct": {"date": "2025-06-01"},
                },
                "sponsorCollaboratorsModule": {
                    "leadSponsor": {"name": "Example Neurology Research Institute"},
                },
                "descriptionModule": {
                    "briefSummary": "This study monitors nerve conduction velocity changes over 24 months in CMT1A patients.",
                },
                "contactsLocationsModule": {
                    "locations": [
                        {"facility": "Example University Medical Center"},
                    ]
                },
            }
        }
    ],
    "nextPageToken": None,
}

SAMPLE_STUDY_MISSING_NCT_ID = {"protocolSection": {"identificationModule": {}}}
