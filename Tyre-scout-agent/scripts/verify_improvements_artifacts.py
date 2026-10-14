"""Verify isolated run artifacts, without network requests or DB mutations."""
from collections import Counter
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
import json
import sqlite3
import sys

from tire_scout.config import load_config
from tire_scout.quality import quote_match, report_section, validate_grounding
from tire_scout.schemas import DevelopmentAnalysis
from tire_scout.scoring import weighted_score

p = Path(sys.argv[1]).resolve()
cfg = load_config('config/config.yaml')
started = json.loads((p / 'started.json').read_text(encoding='utf-8'))
edition = date.fromisoformat(started['edition_date'])
result = json.loads((p / 'result.json').read_text(encoding='utf-8'))
assert result['production_unchanged']
report_path = p / 'reports/daily' / f'daily-{edition}'
report = json.loads(report_path.with_suffix('.json').read_text(encoding='utf-8'))
audits = [json.loads(f.read_text(encoding='utf-8')) for f in (p / 'quality').rglob('*.json')]
by_url = {a['metadata']['canonical_url']: a for a in audits}
accepted = [a for a in audits if a['outcome'] == 'accepted']
for a in audits:
    assert a['outcome'] in ('accepted', 'quarantined', 'unreadable')
    assert len(a['attempts']) <= 2
    for attempt in a['attempts']:
        assert attempt['provider_attempts'], 'Model response audit missing'
for a in accepted:
    analysis = DevelopmentAnalysis.model_validate(a['attempts'][-1]['analysis'])
    problems = validate_grounding(analysis, {**a['metadata'], 'text': a['source_text']}, cfg)
    assert not problems, problems
selected = []
for key, section in [('developments', 'recent'), ('background', 'background'), ('unverified', 'unverified')]:
    for dev in report[key]:
        assert report_section(dev, edition, cfg)[0] == section
        a = by_url[dev['canonical_url']]
        assert a['outcome'] == 'accepted'
        assert all(quote_match(q, a['source_text']) for q in dev['evidence_refs'])
        analysis = DevelopmentAnalysis.model_validate(a['attempts'][-1]['analysis'])
        score, _ = weighted_score(analysis, cfg.scoring.weights)
        assert score == dev['final_score'] and score >= cfg.scoring.inclusion_threshold
        assert dev['source_details']['classification_reason']
        selected.append({'title': dev['title'], 'section': section, 'score': score,
                         'url': dev['canonical_url'], 'fetch_url': dev['source_details']['fetch_url'],
                         'reason': dev['review_reason'], 'source_type': dev['source_type'],
                         'maturity': dev['maturity'], 'confidence': dev['confidence_label']})
assert len(selected) <= cfg.scoring.max_items_daily


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids, self.hrefs, self.scripts = [], [], 0
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get('id'):
            self.ids.append(attrs['id'])
        if attrs.get('href'):
            self.hrefs.append(attrs['href'])
        self.scripts += tag == 'script'


html = Links()
html.feed(report_path.with_suffix('.html').read_text(encoding='utf-8'))
assert len(html.ids) == len(set(html.ids)) and html.scripts == 0
assert all(h[1:] in html.ids for h in html.hrefs if h.startswith('#'))
assert all(d['fetch_url'] in html.hrefs for d in selected)
with sqlite3.connect((p / 'tire_scout.sqlite3').as_uri() + '?mode=ro', uri=True) as conn:
    jobs = conn.execute('SELECT status,error FROM jobs').fetchall()
    assert all(r[0] == 'succeeded' for r in jobs), jobs
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    journal = next(t for t in tables if 'send' in t and 'journal' in t)
    deliveries = [r[0] for r in conn.execute(f'SELECT state FROM {journal}')]
    assert all(s == 'preview' for s in deliveries)
verification = {'status': report['status'], 'duration_minutes': round(result['elapsed_seconds']/60, 2),
                'coverage': report['coverage'], 'selected': selected,
                'audit_outcomes': dict(Counter(a['outcome'] for a in audits)),
                'accepted_without_correction': sum(len(a['attempts']) == 1 for a in accepted),
                'accepted_after_correction': sum(len(a['attempts']) == 2 for a in accepted),
                'checks': 'Passed: quote and claim support, scoring, report sections/cap, original links, HTML anchors, raw-response audits, delivery journal',
                'production_unchanged': True, 'delivery_states': deliveries,
                'limits': ['Isolated database has no previous dedup history.',
                           'Evidence checks do not prove every narrative inference.',
                           'HTML was checked structurally; no live browser layout inspection.']}
(p / 'verification.json').write_text(json.dumps(verification, indent=2) + '\n', encoding='utf-8')
print(json.dumps(verification, indent=2))
