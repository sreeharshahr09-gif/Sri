"""Isolated Qwen preview/replay; never opens the production DB for writing.

Usage: python scripts/verify_improvements_run.py same20|fresh OUTPUT_DIRECTORY
Replay refetches the original 20 URLs, so upstream content may have changed.
"""
from datetime import date, datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import sqlite3
import sys
import time
import winreg

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
from tire_scout.config import apply_telemetry_policy, load_config
from tire_scout import runners
from tire_scout.schemas import QueryPlan, SearchResult


def snapshot():
    out = {}
    for path in [ROOT / 'config/config.yaml', ROOT / 'config/sources.registry.yaml',
                 *(ROOT / 'data/reports').rglob('*')]:
        if path.is_file():
            out[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    for relative in ('data/tire_scout.sqlite3', 'data/checkpoints.sqlite'):
        with sqlite3.connect((ROOT / relative).as_uri() + '?mode=ro', uri=True) as conn:
            digest = hashlib.sha256()
            for line in conn.iterdump():
                digest.update((line + '\n').encode())
            out[relative] = digest.hexdigest()
    return out


def checkpoint(path, edition):
    from langgraph.checkpoint.sqlite import SqliteSaver
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, check_same_thread=False) as conn:
        saver = SqliteSaver(conn)
        saver.is_setup = True
        return saver.get_tuple({'configurable': {'thread_id': 'daily-' + str(edition)}}).checkpoint['channel_values']


def main(kind, output):
    output = Path(output).resolve()
    if not output.is_relative_to(ROOT / 'data/runs'):
        raise ValueError('Verification outputs must stay under data/runs')
    if (output / 'started.json').exists():
        raise ValueError('Run directory already used')
    output.mkdir(parents=True, exist_ok=True)
    def save(name, obj):
        (output / name).write_text(json.dumps(obj, indent=2, default=str) + '\n', encoding='utf-8')
    cfg = load_config(ROOT / 'config/config.yaml').model_copy(deep=True)
    for name in (cfg.search.api_key_env, 'SSL_CERT_FILE'):
        if not os.environ.get(name):
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
                os.environ[name] = winreg.QueryValueEx(key, name)[0]
    cfg.general.data_dir = output
    cfg.email.adapter = 'preview'
    cfg.email.live_sending = cfg.email.daily_enabled = cfg.email.weekly_enabled = False
    apply_telemetry_policy(cfg)
    import httpx
    with httpx.Client(timeout=15) as client:
        client.get(cfg.llm.base_url + '/health').raise_for_status()
        response = client.get(cfg.llm.base_url + '/v1/models')
        response.raise_for_status()
        models = response.json()
        assert 'Qwen3-30B-A3B-Instruct-2507' in json.dumps(models), 'Requested Qwen model is not loaded'
    edition = date(2026, 10, 13) if kind == 'same20' else date(2026, 10, 14)
    before = snapshot()
    save('production-before.json', before)
    save('effective-config.json', cfg.model_dump(mode='json', by_alias=True))
    if kind == 'same20':
        previous = checkpoint(ROOT / 'data/runs/fresh-preview-2026-10-13-02/checkpoints.sqlite', edition)
        hits = previous['search_results']
        assert len(hits) == 20
        save('replayed-search-results.json', hits)
        db, agent_tools, llm = runners.assemble(cfg)
        class ReplaySearch:
            def search(self, query, **kwargs):
                return [SearchResult.model_validate(h) for h in hits]
        def replay_plan(**kwargs):
            return QueryPlan.model_validate({'queries': [{'query': 'Saved 20 URL comparison',
                'source_type': 'news', 'max_results': 20}]})
        llm.plan_queries = replay_plan
        agent_tools.search = ReplaySearch()
        # Bypass discovery filtering only, to assess every previous URL equally.
        cfg.search.provider = 'fixture'
        runners.assemble = lambda *args, **kwargs: (db, agent_tools, llm)
    save('started.json', {'started_at': datetime.now(timezone.utc).isoformat(),
        'kind': kind, 'edition_date': str(edition), 'model_server': models,
        'scope': 'Isolated preview, no previous dedup history; email disabled. '
                 + ('Same 20 URLs refetched; upstream content may change.' if kind == 'same20' else 'Fresh live discovery.'),
        'source_hashes': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in [*(ROOT / 'src/tire_scout').rglob('*.py'), *(ROOT / 'prompts').rglob('*')]
                          if p.is_file()}})
    print('Started ' + kind + ' in ' + str(output), flush=True)
    started = time.monotonic()
    try:
        response = runners.run_daily(cfg, mode='preview', edition_date=edition)
        result = {'ok': response.ok, 'status': response.status, **response.detail}
    except Exception as exc:
        result = {'ok': False, 'status': 'crashed', 'error': f'{type(exc).__name__}: {exc}'}
    result.update(elapsed_seconds=round(time.monotonic() - started, 2),
                  finished_at=datetime.now(timezone.utc).isoformat())
    after = snapshot()
    result['production_unchanged'] = before == after
    save('production-after.json', after)
    save('result.json', result)
    print(json.dumps(result, indent=2), flush=True)
    return 0 if result['ok'] and result['production_unchanged'] else 1


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] not in ('same20', 'fresh'):
        raise SystemExit(__doc__)
    raise SystemExit(main(*sys.argv[1:]))
