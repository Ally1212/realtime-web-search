"""Verify the production-based keyword/eleven-page candidate in isolated PG."""
import json,os,sys,hashlib,unittest
from pathlib import Path
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root));import realtime
artifact=root/'output/google-keyword-expanded-20261117'
if not os.environ.get('TEST_DATABASE_URL'):raise SystemExit('An isolated TEST_DATABASE_URL is required')
manifest=json.loads((artifact/'manifest.json').read_text())
for name,expected in manifest['files'].items():
 if hashlib.sha256((artifact/'candidate'/name).read_bytes()).hexdigest()!=expected:raise SystemExit('Candidate hash changed: '+name)
realtime.__path__.insert(0,str(artifact/'candidate/realtime'))
names=['tests.test_keyword_trends','tests.test_google_expanded_requirements','tests.test_google_live_acceptance','tests.test_middle_collector','tests.test_middle_search_sources','tests.test_middle_live_runtime','tests.test_whale_direct_dispatch','tests.test_google_feeds','tests.test_quick_request_flow','tests.test_product','tests.test_customer_data_api']
# Each excluded assertion was separately reproduced against the unmodified
# production baseline. Do not include unrelated unreleased image/provider work.
excluded={'tests.test_middle_search_sources.SearchSourcesTests.test_images_are_not_disguised_as_web','tests.test_product.ProductIntegrationTests.test_internal_submission_rejects_old_providers_before_reserving','tests.test_product.ProductIntegrationTests.test_retired_local_job_cannot_succeed_from_saved_collector_result','tests.test_customer_data_api.CustomerDataDatabaseTests.test_paused_legacy_job_is_retired_without_reenqueue_or_charge'}
def flatten(suite):
 for item in suite:
  if isinstance(item,unittest.TestSuite):yield from flatten(item)
  else:yield item
suite=unittest.TestSuite(t for t in flatten(unittest.defaultTestLoader.loadTestsFromNames(names)) if t.id() not in excluded)
result=unittest.TextTestRunner(verbosity=2).run(suite)
passed=result.wasSuccessful() and not result.skipped
(artifact/'test-result.json').write_text(json.dumps(dict(success=passed,tests_run=result.testsRun,failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),excluded_known_baseline_failures=sorted(excluded),candidate_files=manifest['files']),indent=2)+'\n')
raise SystemExit(0 if passed else 1)
