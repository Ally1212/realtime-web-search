"""Lossless exports from a Consumer-read, pinned Web run; no DB or uploader.

The pure builder verifies consistency, not authorization. Online callers must
obtain the packet via an authorized Consumer and apply their own tenant gate.
"""
import csv
import io
import json
import zipfile

from .google_data import digest
from .google_data_export import OCCURRENCE_FIELDS, _occurrence_row
from .google_domain import DomainError


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def build_middle_web_bundle(packet):
    try:
        payload, proof = packet['payload'], packet['proof']
        if (payload['schema_version'] != 'google-serp-run-envelope-1'
                or proof['version_readable'] is not True or proof['source_platform'] != 'google_search'
                or not proof['document_id'] or not proof['version_id'] or not proof['dataset_id']):
            raise ValueError()
        observation, source = payload['observation'], payload['source_response']
        if (observation['source'] != 'google_web' or observation['request_id'] != payload['run_id']
                or str(proof['raw_payload_hash']).removeprefix('sha256:') != digest(payload)
                or observation['source_response_hash'] != digest(source)):
            raise ValueError()
        rows = observation['occurrences']
        if not isinstance(rows, list):
            raise ValueError()
        ids = set()
        for row in rows:
            if row['request_id'] != payload['run_id'] or not row['occurrence_id'] or row['occurrence_id'] in ids:
                raise ValueError()
            ids.add(row['occurrence_id'])
        if len(encoded(payload)) > 64 * 1024 * 1024:
            raise ValueError()
        stream = io.StringIO(newline='')
        writer = csv.DictWriter(stream, fieldnames=OCCURRENCE_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(_occurrence_row(row))
        files = {'platform-payload.json': encoded(payload), 'observation.json': encoded(observation),
                 'source-response.json': encoded(source), 'readback-proof.json': encoded(proof),
                 'occurrences.jsonl': b''.join(encoded(row) + b'\n' for row in rows),
                 'occurrences.csv': stream.getvalue().encode('utf-8-sig')}
        if sum(map(len, files.values())) > 128 * 1024 * 1024:
            raise ValueError()
        import hashlib
        manifest = dict(schema_version='searchflow-middle-web-export-1', provenance='consumer_pinned_version',
                        document_id=proof['document_id'], version_id=proof['version_id'],
                        dataset_id=proof['dataset_id'], source_platform=proof['source_platform'],
                        request_id=payload['run_id'], occurrence_count=len(rows),
                        payload_hash=digest(payload), is_truncated=False,
                        files={name:dict(bytes=len(value), sha256=hashlib.sha256(value).hexdigest())
                               for name,value in files.items()})
        files['manifest.json'] = encoded(manifest)
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name,value in files.items():
                info = zipfile.ZipInfo(name, date_time=(1980,1,1,0,0,0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                archive.writestr(info, value)
        return dict(files=files, zip=output.getvalue(), manifest=manifest)
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError):
        raise DomainError('invalid_middle_payload', '中台固定版本不完整、不一致或超过资源上限，未生成导出', 503) from None


def read_middle_web_bundle(client, target):
    """The caller's configured Consumer performs the two existing read calls."""
    return build_middle_web_bundle(client.read_full_run(target))
