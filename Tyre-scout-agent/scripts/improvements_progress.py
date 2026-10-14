"""Read-only progress for an isolated improvements verification run."""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
import sys

p = Path(sys.argv[1]).resolve()
started = json.loads((p / 'started.json').read_text(encoding='utf-8'))
result = {'elapsed_minutes': round((datetime.now(timezone.utc) - datetime.fromisoformat(started['started_at'])).total_seconds()/60, 1)}
if (p / 'tire_scout.sqlite3').exists():
    with sqlite3.connect((p / 'tire_scout.sqlite3').as_uri() + '?mode=ro', uri=True) as conn:
        result['fetch_statuses'] = dict(conn.execute('SELECT status,count(*) FROM sources GROUP BY status').fetchall())
files = sorted((p / 'quality').rglob('*.json'), key=lambda f: f.stat().st_mtime)
items = [json.loads(f.read_text(encoding='utf-8')) for f in files]
result['quality_progress'] = dict(Counter(f"{i['outcome']}: {len(i['attempts'])} attempts" for i in items))
result['recent_sources'] = [{
    'url': i['metadata']['canonical_url'], 'type': i['metadata']['source_type'],
    'outcome': i['outcome'], 'problems': i.get('problems'),
    'date_assessment': i['metadata']['date_assessment'],
    'maturity': next((a['analysis']['maturity'] for a in reversed(i['attempts']) if a.get('analysis')), None)
} for i in items[-3:]]
if (p / 'result.json').exists():
    result['final_result'] = json.loads((p / 'result.json').read_text(encoding='utf-8'))
print(json.dumps(result, indent=2))
