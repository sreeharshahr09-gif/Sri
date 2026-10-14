"""Extract attributable publication dates, separately from modification dates."""
import json
import re
from datetime import datetime
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from dateutil import parser

PUBLICATION = {'article:published_time', 'og:published_time', 'citation_publication_date',
               'citation_date', 'dc.date.issued', 'dcterms.issued', 'datepublished',
               'publicationdate', 'pubdate', 'publishdate', 'parsely-pub-date', 'sailthru.date'}
MODIFIED = {'article:modified_time', 'og:updated_time', 'datemodified', 'last-modified'}
ARTICLE_TYPES = {'Article', 'NewsArticle', 'ScholarlyArticle', 'BlogPosting', 'TechArticle', 'Report', 'WebPage'}


def parse_date(raw):
    # Two defaults expose missing month/day fields instead of inventing them.
    if not isinstance(raw, str) or not re.search(r'\b(?:19|20)\d{2}\b', raw):
        return None
    try:
        first = parser.parse(raw.strip(), fuzzy=False, default=datetime(1900, 1, 1))
        other = parser.parse(raw.strip(), fuzzy=False, default=datetime(1900, 2, 2))
        return first if first == other else None
    except (ValueError, TypeError, OverflowError):
        return None


def _nodes(value):
    if isinstance(value, list):
        for child in value:
            yield from _nodes(child)
    elif isinstance(value, dict):
        yield value
        # Follow the graph, not arbitrary related-article/recommendation properties.
        if '@graph' in value:
            yield from _nodes(value['@graph'])


def extract_page_metadata(html, url):
    soup = BeautifulSoup(html, 'html.parser')
    candidates, modified, hints = [], [], {'schema_types': []}
    is_patent = '/patent/' in urlsplit(url).path

    def add(raw, where, updated=False):
        dt = parse_date(raw)
        if dt:
            (modified if updated else candidates).append({'value': dt.isoformat(), 'raw': raw,
                                                          'location': where})

    for tag in soup.find_all('meta'):
        key = str(tag.get('property') or tag.get('name') or tag.get('itemprop') or '').lower()
        raw = tag.get('content', '')
        if key.startswith('citation_') or key == 'patent_publication_number':
            hints[key] = raw
        if key in PUBLICATION or (key in ('date', 'dc.date') and not is_patent):
            add(raw, 'meta:' + key)
        elif key in MODIFIED:
            add(raw, 'meta:' + key, True)
    canonical_path = urlsplit(url).path.rstrip('/')
    canonical_host = (urlsplit(url).hostname or '').lower().removeprefix('www.')
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            objects = list(_nodes(json.loads(script.string or script.get_text())))
        except (ValueError, TypeError, RecursionError):
            continue
        for obj in objects:
            types = obj.get('@type', [])
            types = [types] if isinstance(types, str) else types
            if not isinstance(types, list):
                continue
            identity = obj.get('url') or obj.get('mainEntityOfPage') or obj.get('@id')
            if isinstance(identity, dict):
                identity = identity.get('@id')
            if isinstance(identity, str) and identity.startswith(('http:', 'https:')):
                identity_url = urlsplit(identity)
                if (identity_url.path.rstrip('/') != canonical_path or
                    (identity_url.hostname or '').lower().removeprefix('www.') != canonical_host):
                    continue
            hints['schema_types'].extend(t for t in types if isinstance(t, str))
            if set(t for t in types if isinstance(t, str)) & ARTICLE_TYPES:
                add(obj.get('datePublished'), 'jsonld:datePublished')
                add(obj.get('dateModified'), 'jsonld:dateModified', True)
    for tag in soup.find_all(['time', 'span', 'div']):
        prop = str(tag.get('itemprop') or '').lower()
        classes = ' '.join(tag.get('class', [])).lower()
        if prop in ('datepublished', 'publicationdate') or (tag.name == 'time' and re.search(r'\b(?:published|pubdate|entry-date)\b', classes)):
            add(tag.get('datetime') or tag.get('content') or tag.get_text(' ', strip=True), 'page_text:published')
        elif prop == 'datemodified' or (tag.name == 'time' and re.search(r'updated|modified', classes)):
            add(tag.get('datetime') or tag.get_text(' ', strip=True), 'page_text:modified', True)
    # Explicit date labels only; bare dates in text may describe old events.
    for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
        tag.decompose()
    text = soup.get_text('\n', strip=True)
    pattern = r'(?im)^\s*(?:Published(?:\s+on)?|Publication date|Posted(?:\s+on)?)\s*:?\s*([^\n]{4,70})'
    for match in re.finditer(pattern, text):
        raw = re.split(r'\s*[|·]\s*|\s+by\s+', match[1], maxsplit=1, flags=re.I)[0]
        add(raw, 'page_text:label')
    days = {c['value'][:10] for c in candidates}
    result = {'published_at': None, 'published_provenance': 'unknown',
              'date_candidates': candidates, 'modified_at': modified[0]['value'] if modified else None,
              'date_notes': [], 'page_metadata': hints}
    if len(days) > 1:
        result['published_provenance'] = 'conflicting'
        result['date_notes'].append('Conflicting publication dates on source; human review required')
    elif candidates:
        chosen = candidates[0]
        result['published_at'] = chosen['value']
        result['published_provenance'] = 'page_text' if chosen['location'].startswith('page_text') else 'page_meta'
    elif modified:
        result['date_notes'].append('Only a modification date is available; publication date remains unknown')
    return result
