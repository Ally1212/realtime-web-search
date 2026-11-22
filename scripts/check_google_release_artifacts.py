"""Read-only gate for the prepared Google release; never modifies servers."""
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'output/google-acceptance-20261117'


def main():
    plan = json.loads((ROOT / 'release-plan.json').read_text())
    manifest = json.loads((ROOT / 'candidate-manifest.json').read_text())
    result = json.loads((ROOT / 'candidate-test-result.json').read_text())
    if not result['success'] or result['skipped'] or result['candidate_files'] != manifest['files']:
        raise SystemExit('Candidate tests do not prove this exact artifact')
    if plan['files'] != manifest['files']:
        raise SystemExit('Release plan file hashes are stale')
    for name, expected in manifest['files'].items():
        data = (ROOT / 'candidate' / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise SystemExit('Candidate changed: ' + name)
        ast.parse(data, filename=name)
    page = (ROOT / 'candidate-index.html').read_bytes()
    if hashlib.sha256(page).hexdigest() != plan['static_candidate_sha256']:
        raise SystemExit('Static candidate changed')
    addition = b",...(source==='google_trends'?{trends_mode:'regional_hotlist'}:{})"
    if page.count(addition) != 1:
        raise SystemExit('Unexpected static patch count')
    if hashlib.sha256(page.replace(addition, b'', 1)).hexdigest() != plan['static_before_sha256']:
        raise SystemExit('Static page contains changes beyond the reviewed patch')
    for name, expected in plan['baseline_hashes'].items():
        if hashlib.sha256((ROOT / 'production-baseline' / name).read_bytes()).hexdigest() != expected:
            raise SystemExit('Production baseline changed: ' + name)
    print(json.dumps(dict(valid=True, candidate_modules=len(manifest['files']),
                          tests=result['tests_run'], skipped=0,
                          static_exact_patch=True, deployment_performed=False)))


if __name__ == '__main__':
    main()
