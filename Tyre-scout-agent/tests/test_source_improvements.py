"""Regressions observed in the October 13 live preview."""
import json
import sqlite3
from datetime import date

import pytest

from conftest import make_development
from test_quality import analysis, source, Scripted
from tire_scout.db import Database, MIGRATIONS
from tire_scout.fetch import Fetcher, FetchResponse
from tire_scout.graphs.daily import build_daily_graph
from tire_scout.page_metadata import extract_page_metadata, parse_date
from tire_scout.quality import claim_problems, analyze_with_quality, report_section
from tire_scout.schemas import QueryPlan, SearchResult
from tire_scout.sources import SourceRegistry, classify_source

DAY = date(2026, 10, 13)


@pytest.fixture
def registry(tmp_path):
    path = tmp_path / 'registry.yaml'
    path.write_text('source_types:\n  company:\n    domains: [www.Michelin.com, "bad domain.com"]\n'
                    '  paper:\n    domains: [mdpi.com]\n  supplier:\n    domains: [evonik.com]\n')
    return SourceRegistry(path)


@pytest.mark.parametrize('url,title,hints,expected', [
    ('https://en.wikipedia.org/wiki/Aramid', 'Aramid', {}, 'reference'),
    ('https://www.michelin.com/tires-101/construction', 'Tire construction', {}, 'reference'),
    ('https://www.prnewswire.com/news-releases/123', 'Smart tire market size and forecast', {}, 'market_report'),
    ('https://www.prnewswire.com/news-releases/123', 'Smart Connected Tires Market to Reach USD 54.211 Billion by 2035', {'schema_types': ['NewsArticle']}, 'market_report'),
    ('https://patents.google.com/patent/US20260257516A1/en', 'Tire', {}, 'patent'),
    ('https://journal.example/a', 'Tire research', {'citation_title': 'Tire research', 'citation_doi': '10.1234/tire'}, 'paper'),
    ('https://www.michelin.com/news/announcement', 'New tire', {}, 'company'),
    ('https://michelin.com.evil.example/news/announcement', 'New tire', {}, 'unknown'),
    ('https://patents.google.com/search', 'Tire search', {}, 'unknown'),
])
def test_document_classification_ignores_planner_hint(registry, url, title, hints, expected):
    result = classify_source({'url': url, 'title': title, 'page_metadata': hints,
                              'planned_source_type': 'patent', 'source_type': 'video'}, registry)
    assert result['source_type'] == expected
    assert result['classification_reason']


def test_registry_normalizes_and_rejects_invalid_domains(registry):
    assert registry.preferred('company') == ['michelin.com']
    assert registry.origin('https://press.michelin.com/a') == 'company'
    assert registry.origin('https://fakemichelin.com/a') is None
    assert len(registry.warnings) == 1


@pytest.mark.parametrize('raw', ['2026', 'October 2026', '2026-10', 'October 13', 'yesterday', '2026-02-31'])
def test_partial_or_invalid_dates_are_not_invented(raw):
    assert parse_date(raw) is None


@pytest.mark.parametrize('markup,provenance', [
    ('<meta content="2026-10-12" property="article:published_time">', 'page_meta'),
    ('<script type="application/ld+json">{"@type":"NewsArticle","datePublished":"2026-10-12", "dateModified":"2026-10-13"}</script>', 'page_meta'),
    ('<time class="published" datetime="2026-10-12">October 12, 2026</time>', 'page_text'),
    ('<p>Published on: October 12, 2026</p>', 'page_text'),
])
def test_publication_metadata_sources(markup, provenance):
    result = extract_page_metadata(markup, 'https://example.com/article')
    assert result['published_at'].startswith('2026-10-12')
    assert result['published_provenance'] == provenance
    assert result['date_candidates'][0]['location']


def test_modified_date_never_becomes_publication_and_conflicts_are_visible():
    update = '<meta property="article:modified_time" content="2026-10-13">'
    result = extract_page_metadata(update, 'https://example.com/a')
    assert result['published_at'] is None and result['modified_at'].startswith('2026-10-13')
    conflict = '<meta property="article:published_time" content="2026-10-10">' + \
               '<time itemprop="datePublished" datetime="2026-10-12"></time>'
    result = extract_page_metadata(conflict + update, 'https://example.com/a')
    assert result['published_at'] is None and result['published_provenance'] == 'conflicting'
    assert len(result['date_candidates']) == 2 and result['date_notes']


def test_related_articles_and_other_hosts_cannot_supply_page_date():
    graph = {'@graph': [
        {'@type': 'NewsArticle', 'url': 'https://example.com/other', 'datePublished': '2026-10-01'},
        {'@type': 'NewsArticle', 'url': 'https://other.example/a', 'datePublished': '2026-10-02'},
        {'@type': 'NewsArticle', 'url': 'https://www.example.com/a', 'datePublished': '2026-10-12',
         'relatedLink': {'@type': 'NewsArticle', 'datePublished': '2026-09-01'}}]}
    result = extract_page_metadata('<script type="application/ld+json">' + json.dumps(graph) + '</script>', 'https://example.com/a')
    assert len(result['date_candidates']) == 1
    assert result['published_at'].startswith('2026-10-12')


def test_patent_generic_date_is_not_publication_date():
    result = extract_page_metadata('<meta name="DC.date" content="2020-01-01">',
                                   'https://patents.google.com/patent/US1234567A')
    assert result['published_at'] is None


@pytest.mark.parametrize('stage,quote', [
    ('prototype', 'Book a demo of our tire inspection solution.'),
    ('prototype', 'We have not developed a prototype yet.'),
    ('lab_result', 'Our laboratory testing equipment is available worldwide.'),
    ('commercial', 'The prototype is not commercially available yet.'),
])
def test_maturity_requires_actual_stage_evidence(stage, quote):
    rec = source(text=quote, interpretation_policy='claims-1.0')
    item = analysis(quote, is_new_development=False, maturity=stage, maturity_quote=quote)
    assert claim_problems(item, rec)


def test_explicit_supported_prototype_is_accepted():
    quote = 'Prototype tires are under evaluation.'
    item = analysis(quote, is_new_development=False, maturity='prototype', maturity_quote=quote)
    assert claim_problems(item, source(text=quote)) == []


def test_supported_claim_is_retained_with_confidence_adjustment_and_audit(cfg):
    quote = 'The supplier introduced a new silane system.'
    rec = source(text=quote, source_type='supplier', interpretation_policy='claims-1.0')
    llm = Scripted(analysis(quote, maturity='supplier_claim', confidence_label='high', novelty_quote=quote))
    result, problems, path = analyze_with_quality(llm, cfg, rec, DAY, [])
    assert not problems and result.confidence_label.value == 'medium'
    audit = json.loads(open(path, encoding='utf-8').read())
    assert audit['attempts'][0]['policy_adjustments']
    assert result.novelty_quote in result.evidence_refs


def test_quantitative_claim_cannot_use_missing_conditions_placeholder():
    quote = 'This compound achieved 12 percent lower rolling resistance.'
    item = analysis(quote, is_new_development=False, maturity='supplier_claim',
                    claimed_benefit='12 percent lower rolling resistance', benefit_quote=quote,
                    baseline_conditions='Not reported')
    assert any('conditions' in p for p in claim_problems(item, source(text=quote)))


def test_market_report_stays_background_even_with_recent_date(cfg):
    rec = source(source_type='market_report', evidence_refs=['Quoted market claim'],
                 maturity='commercial', confidence_label='high')
    assert report_section(rec, DAY, cfg)[0] == 'background'
    quote = 'A new forecast was introduced by the publisher.'
    assert any('background' in p for p in claim_problems(
        analysis(quote, novelty_quote=quote, maturity='supplier_claim'), {**rec, 'text': quote}))


def test_original_www_hostname_reaches_network_and_redirect_is_preserved():
    calls = []
    original = 'https://www.example.com/start?utm_source=search'
    final = 'https://www.example.com/article'
    def transport(url):
        calls.append(url)
        if url == original:
            return FetchResponse(302, {'location': final}, b'', url)
        assert url == final
        return FetchResponse(200, {'content-type': 'text/html'},
                             b'<article>Original hostname must survive canonicalization.</article>', url)
    result = Fetcher(resolve=lambda host: ['93.184.216.34'], transport=transport).fetch(original)
    assert result.status == 'fetched'
    assert calls == [original, final] and result.final_url == final


def test_live_discovery_uses_preferred_domains_then_budgeted_open_fallback(deps):
    deps.cfg.search.provider = 'tavily'
    calls = []
    class Search:
        def search(self, query, **kwargs):
            calls.append(kwargs)
            if kwargs.get('include_domains'):
                return []
            return [SearchResult(title='Tire material', url='https://example.com/article')]
    deps.tools.search = Search()
    deps.llm.plan_queries = lambda **kwargs: QueryPlan.model_validate({'queries': [
        {'query': 'new tire material', 'source_type': 'supplier', 'max_results': 2}]})
    graph = build_daily_graph(deps)
    for update in graph.stream({'edition_date': str(DAY), 'mode': 'preview'}):
        if 'discover' in update:
            result = update['discover']
            break
    assert len(calls) == 2 and 'evonik.com' in calls[0]['include_domains']
    assert 'include_domains' not in calls[1]
    assert result['discovery_strategy'] == {'preferred_queries': 1, 'open_fallback_queries': 1}
    assert deps.tools.budget.searches == 2


def test_interleaved_results_and_original_url_pass_through_daily_graph(deps):
    urls = [f'https://www.example.com/{i}' for i in range(4)]
    class Search:
        def search(self, query, **kwargs):
            group = urls[:2] if query == 'first query' else urls[2:]
            return [SearchResult(title=u, url=u) for u in group]
    deps.tools.search = Search()
    deps.llm.plan_queries = lambda **kwargs: QueryPlan.model_validate({'queries': [
        {'query': q, 'source_type': 'patent', 'max_results': 2} for q in ('first query', 'second query')]})
    calls = []
    def fetch(args):
        calls.append(args.url)
        return {'url': args.url, 'final_url': args.url, 'status': 'fetched',
                'text': 'Some real source text', 'extraction_quality': 'full'}
    deps.tools.fetch_source = fetch
    graph = build_daily_graph(deps)
    for update in graph.stream({'edition_date': str(DAY), 'mode': 'preview'}):
        if 'fetch_extract' in update:
            result = update['fetch_extract']
            break
    assert calls == [urls[0], urls[2], urls[1], urls[3]]
    assert all(r['source_type'] == 'unknown' for r in result['fetched'])


def test_schema_v1_migration_preserves_legacy_rows_and_new_details(tmp_path):
    path = tmp_path / 'legacy.sqlite'
    with sqlite3.connect(path) as conn:
        conn.executescript(MIGRATIONS[0])
        conn.execute("INSERT INTO schema_migrations VALUES(1, '2026-10-13')")
        conn.execute("INSERT INTO developments(id,title,canonical_url,discovered_at,edition_date,status,created_at) "
                     "VALUES('legacy','Preserved','https://example.com/legacy','2026-10-13','2026-10-13','active','2026-10-13')")
    db = Database(path)
    try:
        rows = db.developments_for_edition(DAY)
        assert rows[0]['title'] == 'Preserved' and rows[0]['source_details'] == {}
        dev = make_development('new', DAY, 7, 'New source')
        dev.source_details = {'fetch_url': 'https://www.example.com/new',
                              'date_notes': ['Conflict needs review'], 'claim_support': {'novelty_quote': None}}
        db.insert_development(dev)
        row = next(r for r in db.developments_for_edition(DAY) if r['id'] == 'new')
        assert row['source_details'] == dev.source_details
        assert db._conn.execute('SELECT max(version) FROM schema_migrations').fetchone()[0] == 2
    finally:
        db.close()


def test_html_and_pdf_links_keep_working_original_hostname(cfg, db, tmp_path):
    from bs4 import BeautifulSoup
    from pypdf import PdfReader
    from tire_scout.report.render import render_daily, render_weekly
    original = 'https://www.example.com/new?view=technical&lang=en'
    dev = make_development('linked', DAY, 7, 'Technical source')
    dev.source_details = {'fetch_url': original}
    db.insert_development(dev)
    db.upsert_daily_edition(DAY, 'research_complete', {}, {}, {}, {})
    cfg.email.daily_pdf = True
    daily = render_daily(cfg, db, DAY)
    weekly = render_weekly(cfg, db, date(2026, 10, 12), priority_ids=['linked'], additional_ids=[],
                           themes=[], actions=[], partial=True, missing_dates=[])
    for output in (daily, weekly):
        html = BeautifulSoup(open(output['html'], encoding='utf-8').read(), 'html.parser')
        assert any(a.get('href') == original for a in html.find_all('a'))
        links = [a.get_object().get('/A', {}).get('/URI') for page in PdfReader(output['pdf']).pages
                 for a in page.get('/Annots', [])]
        assert original in links
