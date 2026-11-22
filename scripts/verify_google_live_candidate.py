"""Verify the exact recorded Google candidate against behavioral/database gates.

Run with TEST_DATABASE_URL pointing to an isolated PostgreSQL instance.
Never connects to production or starts search collection.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import realtime


def main():
    if not os.environ.get('TEST_DATABASE_URL'):
        raise SystemExit('TEST_DATABASE_URL is required; skipped database gates are not acceptance')
    artifact = ROOT / 'output/google-acceptance-20261117'
    manifest = json.loads((artifact / 'candidate-manifest.json').read_text())
    candidate = artifact / 'candidate'
    for name, expected in manifest['files'].items():
        if hashlib.sha256((candidate / name).read_bytes()).hexdigest() != expected:
            raise SystemExit('Candidate hash mismatch: ' + name)
    runtime = ast.parse((candidate / 'realtime/middle_live_runtime.py').read_text())
    main_line = next(n.lineno for n in runtime.body if isinstance(n, ast.If)
                     and '__name__' in ast.unparse(n.test))
    for name in ('selected_pages', 'combined_observation', 'collection_coverage', 'observation_quality'):
        definition = next(n for n in runtime.body if isinstance(n, ast.FunctionDef) and n.name == name)
        if definition.lineno > main_line:
            raise SystemExit('Helper must be defined before runner starts: ' + name)
    realtime.__path__.insert(0, str(candidate / 'realtime'))
    names = ['tests.test_google_live_acceptance',
             'tests.test_whale_direct_dispatch.DirectDatabaseTests',
             'tests.test_middle_maps_contract', 'tests.test_middle_maps_collector',
             'tests.test_middle_maps_runtime', 'tests.test_maps_text_dispatch']
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(names))
    success = result.wasSuccessful() and not result.skipped
    (artifact / 'candidate-test-result.json').write_text(json.dumps(dict(
        success=success, tests_run=result.testsRun, failures=len(result.failures),
        errors=len(result.errors), skipped=len(result.skipped),
        candidate_files=manifest['files']), indent=2)+'\n')
    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
