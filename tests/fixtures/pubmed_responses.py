"""Recorded-shape (hand-built, not live) PubMed efetch XML fixture, matching
the real NCBI EFetch schema closely enough to exercise app.collectors.pubmed
.parse_pubmed_xml() with zero network access (spec #45, #48)."""

SAMPLE_EFETCH_XML = """<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>10000099</PMID>
      <Article>
        <Journal>
          <JournalIssue>
            <PubDate>
              <Year>2025</Year>
              <Month>Jun</Month>
              <Day>10</Day>
            </PubDate>
          </JournalIssue>
          <Title>Journal of Peripheral Nerve Research</Title>
        </Journal>
        <ArticleTitle>PMP22 dosage sensitivity and axonal degeneration in CMT1A mouse models</ArticleTitle>
        <Abstract>
          <AbstractText>We characterize axonal degeneration mechanisms in PMP22-overexpressing CMT1A mouse models.</AbstractText>
        </Abstract>
        <AuthorList>
          <Author>
            <LastName>Smith</LastName>
            <ForeName>Jane</ForeName>
          </Author>
          <Author>
            <LastName>Doe</LastName>
            <ForeName>John</ForeName>
          </Author>
        </AuthorList>
        <ELocationID EIdType="doi">10.1000/pmp22-mouse-model</ELocationID>
      </Article>
    </MedlineCitation>
    <PubmedData>
      <ArticleIdList>
        <ArticleId IdType="pubmed">10000099</ArticleId>
        <ArticleId IdType="doi">10.1000/pmp22-mouse-model</ArticleId>
      </ArticleIdList>
    </PubmedData>
  </PubmedArticle>
</PubmedArticleSet>
"""

SAMPLE_ESEARCH_JSON = {
    "esearchresult": {
        "count": "1",
        "retmax": "1",
        "retstart": "0",
        "idlist": ["10000099"],
    }
}
