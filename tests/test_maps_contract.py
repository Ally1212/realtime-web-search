import unittest

from realtime.product_http import data_openapi
from realtime.maps_provider import normalize, prepare
from realtime.google_local_jobs import parameters
from realtime.maps_export import content


class MapsContractTests(unittest.TestCase):
    def test_main_capabilities_discover_maps_as_a_separate_api(self):
        from realtime.product import capability_payload
        d=capability_payload()
        self.assertEqual(d['maps_api']['jobs_url'],'/v1/google/local/jobs')
        self.assertEqual(d['maps_api']['global_active_limit'],4)
        self.assertFalse(d['maps_api']['middle_enabled'])
        self.assertNotIn('google_maps',[s['id'] for s in d['sources']])

    def test_every_maps_path_parameter_and_download_format_is_documented(self):
        doc=data_openapi()
        paths={p:o for p,o in doc['paths'].items() if p.startswith('/v1/google/local/')}
        self.assertEqual(len(paths),5)
        for path,operations in paths.items():
            for operation in operations.values():
                if '{id}' in path:
                    self.assertIn('id',[p['name'] for p in operation['parameters'] if p['in']=='path' and p['required']])
                self.assertIn('429',operation['responses'])
        op=paths['/v1/google/local/jobs/{id}/export']['get']
        self.assertIn('format',[p['name'] for p in op['parameters'] if p.get('required')])
        self.assertIn('text/csv',op['responses']['200']['content'])
        self.assertIn('MapsPlace',doc['components']['schemas'])
        self.assertNotIn('/v1/google/places/{place_id}',doc['paths'])

    def test_sparse_and_empty_results_keep_exact_source_provenance(self):
        import csv,io,json
        raw=dict(title='Sparse shop',data_id='1:2',latitude=1.29,longitude=103.85,extra={'nested':['kept']})
        rows=normalize([raw],prepare(parameters(dict(query='shop',city='Singapore'))))
        self.assertIsNone(rows[0]['rating'])
        self.assertEqual(rows[0]['source_fields'],raw)
        job=dict(id='test',parameters={'query':'shop'},completed_at='now',result_cache={
            'places':rows,'provider':'gosom','provider_version':'test'})
        exported=list(csv.DictReader(io.StringIO(content(job,'csv'))))
        self.assertEqual(json.loads(exported[0]['record_json']),rows[0])
        job['result_cache']['places']=[]
        self.assertEqual(json.loads(content(job,'jsonl')),job)
        self.assertEqual(list(csv.DictReader(io.StringIO(content(job,'csv')))),[])

    def test_pinned_provider_nil_slice_is_a_valid_empty_result(self):
        p=prepare(parameters(dict(query='coffee',location=dict(lat=-40,lng=-120),radius_m=1)))
        self.assertEqual(normalize([None],p),[])
        self.assertEqual(normalize([[]],p),[])

    def test_empty_output_is_only_accepted_after_successful_provider_job(self):
        import hashlib,tempfile
        from pathlib import Path
        from unittest.mock import patch
        from realtime.maps_provider import search
        from realtime.product import ProductError
        p=parameters(dict(query='coffee',city='Singapore'))
        with tempfile.TemporaryDirectory() as folder:
            binary=Path(folder)/'fixture';binary.write_bytes(b'fixture')
            for log,output,expected in [('{"status":"success"}','null\n',True),
                    ('{"status":"success"}','',False),
                    ('{"status":"error"}','null\n',False),
                    ('{"status":"success"}','broken',False)]:
                def launch(args,**kwargs):
                    kwargs['stdout'].write(log.encode());kwargs['stdout'].flush()
                    Path(args[args.index('-results')+1]).write_text(output)
                    class Process:
                        def wait(self,timeout):return 0
                    return Process()
                with self.subTest(log=log,output=output),patch('realtime.maps_provider.config',return_value={'enabled':True,'binary':str(binary)}),patch('realtime.maps_provider.BINARY_SHA256',hashlib.sha256(b'fixture').hexdigest()),patch('realtime.maps_provider.subprocess.Popen',side_effect=launch):
                    if expected:self.assertEqual(search(p)['places'],[])
                    else:
                        with self.assertRaises(ProductError):search(p)
