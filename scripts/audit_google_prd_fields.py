#!/usr/bin/env python3
"""Offline field coverage from captured public requests; never queries a source."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from realtime.google_data import timestamp
from realtime.google_field_adaptation import adapt_request_fields, FIELD_VERSION


def audit(request_directory, output, *, since=None, until=None):
    if output.exists():
        raise FileExistsError('Use a new output directory')
    bounds = [timestamp(v) if v is not None else None for v in (since, until)]
    if any(v is not None and b is None for v, b in zip((since, until), bounds)):
        raise ValueError('Bounds require timezone-aware ISO timestamps')
    bounds = [datetime.fromisoformat(b.replace('Z','+00:00')) if b else None for b in bounds]
    if all(bounds) and bounds[0] >= bounds[1]:
        raise ValueError('since must precede until')
    rows, input_hashes = {}, {}
    for path in sorted(request_directory.glob('*.json')):
        document = json.loads(path.read_text())
        if not isinstance(document, dict) or not isinstance(document.get('results'), list):
            raise ValueError('Expected captured browser results')
        input_hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        for response in document['results']:
            body = response.get('body')
            if response.get('status') != 200 or not isinstance(body, dict) or body.get('has_more'):
                raise ValueError('Captured request page failed or was truncated')
            for row in body['rows']:
                when = timestamp(row['completed_at'])
                if when is None:
                    raise ValueError('Captured completion time is invalid')
                when = datetime.fromisoformat(when.replace('Z','+00:00'))
                if bounds[0] and when < bounds[0] or bounds[1] and when >= bounds[1]:
                    continue
                if row['id'] in rows and rows[row['id']] != row:
                    raise ValueError('Conflicting snapshots of the same request')
                rows[row['id']] = row
    output.mkdir(parents=True)
    sources, field_counts, item_fields, feature_fields = defaultdict(Counter), Counter(), Counter(), Counter()
    counts = Counter()
    adapted = []
    for row in sorted(rows.values(), key=lambda r:(r['completed_at'], r['id'])):
        response = row['response']
        pagination = response.get('pagination') or {}
        # The browser request projection omits the SQL page column. Preserve
        # only an explicit response page; never silently assume page 1.
        page = pagination.get('page') if isinstance(pagination, dict) else None
        page = page if type(page) is int and page >= 1 else None
        value = adapt_request_fields(dict(request_id=row['id'], source=row['source'],
            query=row['query'], page=page, classification=row['status'],
            completed_at=row['completed_at'], response=response))
        adapted.append(value)
        source = sources[row['source']]
        source['requests'] += 1
        successful = row['status'] in ('results','empty')
        source['successful'] += successful
        source['occurrences'] += len(value['occurrences'])
        source['topics'] += len(value['topics'])
        source['features'] += len(value['field_adaptation']['features'])
        counts['requests'] += 1
        counts['successful_requests'] += successful
        if successful:
            for name, field in value['field_adaptation']['run'].items():
                field_counts[name] += field['value'] is not None
        for item in value['field_adaptation']['items']:
            for name, v in item.items():
                if name not in ('missing_reason',):
                    item_fields[name] += v is not None
        for feature in value['field_adaptation']['features']:
            for name in ('native_id','block_order','expanded_state','channel','text'):
                feature_fields[name] += feature[name] is not None
            counts['feature_blocks'] += 1
            counts['explicit_citation_urls'] += len(feature['citation_urls'])
            counts['generic_feature_target_urls'] += len(feature['target_urls'])
            counts['feature_child_records'] += len(feature['children'])
            counts['separate_source_answers'] += sum(c['answer_text'] is not None for c in feature['children'])
    (output/'adapted-requests.jsonl').write_text(''.join(json.dumps(v,ensure_ascii=False,separators=(',',':'))+'\n' for v in adapted))
    report = dict(version=FIELD_VERSION, input_scope='captured_public_request_projection_not_a_database_snapshot',
        input_limitations=['SQL page/provider columns are absent unless explicit response fields exist',
                           'source freshness/cache/rights cannot be inferred from successful HTTP',
                           'coverage is this fixed sample, not all historical data or the web'],
        requests=counts['requests'], successful_requests=counts['successful_requests'],
        sources={s:dict(c) for s,c in sources.items()},
        run_fields_successful={k:dict(provided=v,eligible=counts['successful_requests']) for k,v in field_counts.items()},
        item_fields=dict(item_fields), feature_fields=dict(feature_fields), counts=dict(counts),
        input_sha256=input_hashes, source_calls=0, platform_writes=0)
    (output/'field-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    manifest = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.iterdir())}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('request_directory',type=Path)
    p.add_argument('output',type=Path)
    p.add_argument('--since')
    p.add_argument('--until')
    args = p.parse_args()
    result = audit(args.request_directory,args.output,since=args.since,until=args.until)
    print(json.dumps({k:result[k] for k in ('requests','successful_requests','sources','counts','source_calls','platform_writes')},ensure_ascii=False))
