import pytest

from pmid2endnote.errors import PubMedParseError
from pmid2endnote.pubmed import batch_pmids, parse_pubmed_xml


SAMPLE_XML = """\
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>12345678</PMID>
      <Article>
        <ArticleTitle>Example <i>formatted</i> title.</ArticleTitle>
        <Journal>
          <Title>Journal of Tests</Title>
          <JournalIssue>
            <PubDate><Year>2023</Year></PubDate>
          </JournalIssue>
        </Journal>
        <ArticleDate><Year>2024</Year></ArticleDate>
        <AuthorList>
          <Author><LastName>Smith</LastName><ForeName>Ada</ForeName></Author>
        </AuthorList>
        <ELocationID EIdType="doi">10.1000/test</ELocationID>
        <PublicationTypeList>
          <PublicationType>Retracted Publication</PublicationType>
        </PublicationTypeList>
      </Article>
      <CommentsCorrectionsList>
        <CommentsCorrections RefType="RetractionIn" />
      </CommentsCorrectionsList>
    </MedlineCitation>
    <PubmedData>
      <ArticleIdList>
        <ArticleId IdType="pubmed">12345678</ArticleId>
        <ArticleId IdType="doi">10.1000/from-article-id</ArticleId>
      </ArticleIdList>
    </PubmedData>
  </PubmedArticle>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>23456789</PMID>
      <Article>
        <ArticleTitle>Collective author paper.</ArticleTitle>
        <Journal>
          <JournalIssue>
            <PubDate><MedlineDate>Winter 1999-2000</MedlineDate></PubDate>
          </JournalIssue>
        </Journal>
        <AuthorList>
          <Author><CollectiveName>Genome Consortium</CollectiveName></Author>
        </AuthorList>
      </Article>
    </MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>
"""


def test_pubmed_xml_parsing_from_sample_xml() -> None:
    records = parse_pubmed_xml(SAMPLE_XML)

    assert len(records) == 2
    assert records[0].pmid == "12345678"
    assert records[0].first_author == "Smith"
    assert records[0].year == "2024"
    assert records[0].title == "Example formatted title."
    assert records[0].journal == "Journal of Tests"
    assert records[0].doi == "10.1000/from-article-id"
    assert records[0].retraction_update_flags == ("RetractionIn", "Retracted Publication")
    assert records[1].first_author == "Genome Consortium"
    assert records[1].year == "1999"


def test_malformed_pubmed_xml_raises_diagnostic_error() -> None:
    with pytest.raises(PubMedParseError, match="Malformed PubMed XML"):
        parse_pubmed_xml("<PubmedArticleSet>")


def test_batch_generation_for_ncbi_requests() -> None:
    assert batch_pmids(["1", "2", "3", "4", "5"], batch_size=2) == [
        ["1", "2"],
        ["3", "4"],
        ["5"],
    ]
