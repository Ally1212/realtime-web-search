"""SearchFlow Maps client (Python 3.11+, standard library only).

Set SEARCHFLOW_API_KEY and SEARCHFLOW_ACCESS_KEY, then:
  python examples/maps_api.py --query coffee --city Singapore
Retry an uncertain submission with the SAME --idempotency-key.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import UUID, uuid4


class APIError(RuntimeError):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(f'HTTP {status}: {code}')


class MapsClient:
    def __init__(self, api_key, access_key, base_url='https://searchflow.20-150-145-162.sslip.io'):
        if not base_url.startswith('https://') or not api_key or not access_key:
            raise ValueError('HTTPS base URL and both credentials are required')
        self.base_url = base_url.rstrip('/')
        self.headers = {'Authorization': 'Bearer '+api_key, 'X-SearchFlow-Access-Key': access_key}

    def _request(self, path, *, payload=None, idem=None, binary=False):
        headers = {**self.headers, 'Accept': '*/*' if binary else 'application/json'}
        data = None
        if payload is not None:
            headers['Content-Type'] = 'application/json'
            data = json.dumps(payload, ensure_ascii=False).encode()
        if idem:
            headers['Idempotency-Key'] = idem
        request = Request(self.base_url+path, data=data, headers=headers)
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read()
        except HTTPError as exc:
            try:
                code = json.loads(exc.read()).get('code', 'request_failed')
            except (ValueError, AttributeError):
                code = 'request_failed'
            raise APIError(exc.code, code) from None
        return raw if binary else json.loads(raw)

    def submit(self, parameters, idempotency_key):
        if not idempotency_key:
            raise ValueError('Persist an idempotency key before submitting')
        return self._request('/v1/google/local/jobs', payload=parameters, idem=idempotency_key)

    @staticmethod
    def _path(job_id):
        return '/v1/google/local/jobs/'+str(UUID(job_id))

    def status(self, job_id):
        return self._request(self._path(job_id))

    def wait(self, job_id, timeout=360, interval=3):
        if timeout <= 0 or interval < 1:
            raise ValueError('Positive timeout and polling interval >= 1 second required')
        deadline = time.monotonic()+timeout
        while True:
            job = self.status(job_id)
            if job['status'] in ('succeeded', 'failed'):
                return job
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Job still pending; resume polling the same job ID')
            time.sleep(min(interval, remaining))

    def results(self, job_id):
        return self._request(self._path(job_id)+'/results')

    def export(self, job_id, format='csv'):
        if format not in ('csv', 'jsonl'):
            raise ValueError('format must be csv or jsonl')
        return self._request(self._path(job_id)+'/export?format='+format, binary=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--query', default='coffee')
    parser.add_argument('--city', default='Singapore')
    parser.add_argument('--radius-m', type=int, default=5000)
    parser.add_argument('--language', choices=['en', 'zh'], default='en')
    parser.add_argument('--idempotency-key', default=None)
    parser.add_argument('--job-id', help='Resume an existing job without submitting a new query')
    parser.add_argument('--output', default='maps-output')
    args = parser.parse_args()
    client = MapsClient(os.environ.get('SEARCHFLOW_API_KEY', ''),
                        os.environ.get('SEARCHFLOW_ACCESS_KEY', ''),
                        os.environ.get('SEARCHFLOW_BASE_URL', 'https://searchflow.20-150-145-162.sslip.io'))
    output = Path(args.output); output.mkdir(parents=True, exist_ok=True)
    if args.job_id:
        job_id = args.job_id
    else:
        idem = args.idempotency_key or 'maps-python-'+uuid4().hex
        # Persist before any network action so a connection failure is recoverable.
        (output/'submission.json').write_text(json.dumps({'idempotency_key': idem,
            'parameters': dict(mode='text', query=args.query, city=args.city,
                               radius_m=args.radius_m, language=args.language)}, ensure_ascii=False, indent=2))
        saved = json.loads((output/'submission.json').read_text())
        job = client.submit(saved['parameters'], idem)
        job_id = job['id']
        (output/'job.json').write_text(json.dumps(job, indent=2))
    print('Job ID:', job_id)
    job = client.wait(job_id)
    if job['status'] == 'failed':
        raise APIError(200, job.get('error_code', 'job_failed'))
    result = client.results(job_id)
    (output/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    for format in ('csv', 'jsonl'):
        (output/('maps.'+format)).write_bytes(client.export(job_id, format))
    print('Places:', len(result['result_cache']['places']), '| Files:', output)


if __name__ == '__main__':
    main()
