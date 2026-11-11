"""Complete fixed-observation export, with independently checkable artifacts.

No 7/14-column SERP conversion. CSV record_json is the machine authority;
spreadsheet-facing identity columns are formula escaped. ZIP is only returned
once every file and manifest has been built successfully.
"""
from __future__ import annotations

from collections import Counter
import copy
import csv
import hashlib
import io
import json
import threading
import zipfile
import zlib

from .data_capability_jobs import digest
from .data_capability_store import projections
from .product import ProductError

VERSION = 'searchflow-facts-export-1'
MAX_EXPORT_BYTES = 256 * 1024 * 1024
MAX_RECORD_BYTES = MAX_EXPORT_BYTES
_COLUMNS = ('record_type', 'parent_id', 'source_id', 'position', 'record_json')
_EXPORT_SLOTS = threading.BoundedSemaphore(2)


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def rows(observation):
    header = copy.deepcopy(observation)
    data = header['payload']['data']
    for key in ('series', 'groups', 'answer'):
        data.pop(key, None)
    yield dict(record_type='observation', parent_id='', source_id=observation['observation_id'], position=0, payload=header)
    for kind, parent, source_id, position, payload in projections(observation['payload']):
        yield dict(record_type=kind, parent_id=parent, source_id=source_id, position=position, payload=payload)


def reconstruct(records):
    if not records or records[0]['record_type'] != 'observation':
        raise ValueError('missing snapshot header')
    header = copy.deepcopy(records[0]['payload'])
    capability = header['capability']
    data = header['payload']['data']
    parents = {}
    for row in records[1:]:
        kind, parent, payload = row['record_type'], row['parent_id'], copy.deepcopy(row['payload'])
        if kind in ('series', 'volume', 'answer'):
            if parent:
                raise ValueError('invalid root parent')
            key = 'series' if kind == 'series' else 'groups' if kind == 'volume' else 'answer'
            if kind == 'answer':
                if key in data:
                    raise ValueError('duplicate answer')
                payload.update(blocks=[], citations=[], links=[])
                data[key] = payload
            else:
                payload['points' if kind == 'series' else 'monthly'] = []
                data.setdefault(key, []).append(payload)
            parents[row['source_id']] = payload
        else:
            key = {'point': 'points', 'month': 'monthly', 'block': 'blocks', 'citation': 'citations', 'link': 'links'}[kind]
            parents[parent][key].append(payload)
    if capability == 'trends_history':
        data.setdefault('series', [])
    elif capability == 'keyword_volume':
        data.setdefault('groups', [])
    return header


def _safe_cell(value):
    if isinstance(value, str) and (value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n'))):
        return "'" + value
    return value


def build_bundle(observation):
    if not _EXPORT_SLOTS.acquire(blocking=False):
        raise ProductError('export_busy', '导出并发已满，请稍后重试', 429)
    try:
        return _build(observation)
    finally:
        _EXPORT_SLOTS.release()


def _build(observation):
    records = list(rows(observation))
    document = encoded(observation) + b'\n'
    jsonl = b''.join(encoded(row) + b'\n' for row in records)
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(_COLUMNS)
    for row in records:
        writer.writerow([_safe_cell(row[k]) for k in _COLUMNS[:-1]] + [encoded(row).decode()])
    spreadsheet = stream.getvalue().encode('utf-8-sig')
    files = {'observation.json': document, 'records.jsonl': jsonl, 'records.csv': spreadsheet}
    if sum(map(len, files.values())) > MAX_EXPORT_BYTES:
        raise ProductError('export_too_large', '完整导出超过资源上限；未发布文件', 413)
    counts = dict(Counter(row['record_type'] for row in records))
    payload = observation['payload']
    manifest = dict(version=VERSION, observation_id=observation['observation_id'], capability=observation['capability'],
                    payload_hash=observation['payload_hash'], snapshot_hash=observation['snapshot_hash'], schema_version=payload['schema_version'],
                    snapshot_stored_at=observation['stored_at'], expires_at=observation['expires_at'],
                    parameters=observation['parameters'], effective_parameters=payload['effective_parameters'],
                    is_partial=payload['is_partial'], is_truncated=payload['is_truncated'],
                    record_counts=counts, input_count=len(payload['data'].get('input_mapping', [])),
                    raw_status=payload['raw']['status'], hash_encoding=observation['hash_encoding'],
                    csv_formula_escape='apostrophe_prefix_on_identity_columns; record_json_is_unmodified_authority',
                    citation_text_offsets='unicode_codepoints_start_inclusive_end_exclusive',
                    files={name: dict(bytes=len(content), sha256=sha(content)) for name, content in files.items()})
    files['manifest.json'] = encoded(manifest) + b'\n'
    if sum(map(len, files.values())) > MAX_EXPORT_BYTES:
        raise ProductError('export_too_large', '完整导出超过资源上限；未发布文件', 413)
    target = io.BytesIO()
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100600 << 16
            archive.writestr(info, content)
    if target.tell() > MAX_EXPORT_BYTES:
        raise ProductError('export_too_large', '完整导出超过资源上限；未发布文件', 413)
    files['bundle.zip'] = target.getvalue()
    return dict(files=files, manifest=manifest, zip_sha256=sha(files['bundle.zip']))


def verify_bundle(content, *, expected_payload_hash, expected_observation_id, expected_snapshot_hash):
    """Verify against an independently obtained authorized snapshot identity."""
    try:
        if len(content) > MAX_EXPORT_BYTES:
            raise ValueError('archive too large')
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
            names = [i.filename for i in infos]
            required = {'observation.json', 'records.jsonl', 'records.csv', 'manifest.json'}
            if len(names) != len(required) or set(names) != required or sum(i.file_size for i in infos) > MAX_EXPORT_BYTES:
                raise ValueError('invalid archive members or size')
            files = {name: archive.read(name) for name in required}
        manifest = json.loads(files['manifest.json'])
        if (manifest['version'] != VERSION or manifest['payload_hash'] != expected_payload_hash
                or manifest['observation_id'] != expected_observation_id or manifest['snapshot_hash'] != expected_snapshot_hash
                or set(manifest['files']) != required - {'manifest.json'}):
            raise ValueError('manifest identity mismatch')
        for name, proof in manifest['files'].items():
            if proof != dict(bytes=len(files[name]), sha256=sha(files[name])):
                raise ValueError('file checksum mismatch')
        observation = json.loads(files['observation.json'])
        if (observation['observation_id'] != expected_observation_id or digest(observation['payload']) != expected_payload_hash
                or observation['payload_hash'] != expected_payload_hash):
            raise ValueError('payload identity mismatch')
        metadata = {k: v for k, v in observation.items() if k != 'snapshot_hash'}
        if digest(metadata) != expected_snapshot_hash or observation['snapshot_hash'] != expected_snapshot_hash:
            raise ValueError('snapshot metadata mismatch')
        raw = observation['payload']['raw']['payload']
        if observation['raw_hash'] != (digest(raw) if raw is not None else None):
            raise ValueError('raw hash mismatch')
        expected = list(rows(observation))
        jsonl = [json.loads(line) for line in files['records.jsonl'].splitlines()]
        # Existing complete-data verifier uses this same process-wide CSV limit;
        # do not reduce it while concurrent CSV readers may be in progress.
        if csv.field_size_limit() < MAX_RECORD_BYTES:
            csv.field_size_limit(MAX_RECORD_BYTES)
        reader = csv.DictReader(io.StringIO(files['records.csv'].decode('utf-8-sig')))
        if tuple(reader.fieldnames or ()) != _COLUMNS:
            raise ValueError('CSV schema mismatch')
        csv_records = []
        for row in reader:
            record = json.loads(row['record_json'])
            if any(row[key] != str(_safe_cell(record[key])) for key in _COLUMNS[:-1]):
                raise ValueError('CSV identity columns mismatch')
            csv_records.append(record)
        if expected != jsonl or expected != csv_records or reconstruct(jsonl) != observation:
            raise ValueError('records or parent relations mismatch')
        counts = dict(Counter(row['record_type'] for row in expected))
        payload = observation['payload']
        if (manifest['record_counts'] != counts or manifest['input_count'] != len(payload['data'].get('input_mapping', []))
                or manifest['is_partial'] != payload['is_partial'] or manifest['is_truncated'] != payload['is_truncated']
                or manifest['parameters'] != observation['parameters'] or manifest['effective_parameters'] != payload['effective_parameters']
                or manifest['expires_at'] != observation['expires_at'] or manifest['raw_status'] != payload['raw']['status']):
            raise ValueError('manifest scope/count mismatch')
        return manifest
    except (ValueError, KeyError, TypeError, UnicodeError, csv.Error, zipfile.BadZipFile, RuntimeError, EOFError, OSError, zlib.error):
        raise ProductError('corrupt_bundle', '导出文件、身份、数量或父子关系校验失败') from None


def export_job(service, tenant_id, jid, grant, format='zip'):
    if format not in ('json', 'jsonl', 'csv', 'zip', 'manifest'):
        raise ProductError('invalid_parameter', 'format仅支持json/jsonl/csv/zip/manifest')
    # Authorization is checked on every generation. No durable download file
    # survives revocation here; externally downloaded copies cannot be recalled.
    saved = service.job_data(tenant_id, jid, grant)
    built = build_bundle(saved['observation'])
    current = service.job_data(tenant_id, jid, grant)
    if current['observation']['snapshot_hash'] != saved['observation']['snapshot_hash']:
        raise ProductError('version_conflict', '导出生成期间事实版本改变', 409)
    name = {'json': 'observation.json', 'jsonl': 'records.jsonl', 'csv': 'records.csv',
            'zip': 'bundle.zip', 'manifest': 'manifest.json'}[format]
    return built['files'][name], dict(observation_id=saved['observation']['observation_id'],
                                     payload_hash=saved['observation']['payload_hash'],
                                     snapshot_hash=saved['observation']['snapshot_hash'],
                                     sha256=sha(built['files'][name]), bytes=len(built['files'][name]))
