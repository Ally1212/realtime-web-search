import json
from types import SimpleNamespace
import unittest

from realtime.serp_fields import organic_result,response_envelope,public_fields
from realtime.google_feeds import parse_feed,traffic_fields
from realtime.serp_presentation import user_result_rows,csv_value
from tests.test_google_feeds import NEWS,TRENDS


class FieldExpansionTests(unittest.TestCase):
    def test_final_site_is_separate_from_google_wrapper_and_upstream_signals(self):
        row=organic_result({'id':'r1','type':'organic','engine':'google','rank':1,
                            'domain':'google.com','favicon':'https://google.com/favicon.ico',
                            'url':'https://www.google.com/goto?x=1',
                            'classification':{'content_type':'article'},'domain_info':{'category':'news'}},
                           url='https://publisher.example/story',page=1)
        self.assertEqual(row['domain'],'publisher.example')
        self.assertEqual(row['upstream_domain'],'google.com')
        self.assertIsNone(row['favicon'])
        self.assertEqual(row['upstream_favicon'],'https://google.com/favicon.ico')
        self.assertEqual(row['classification'],{'content_type':'article'})
        self.assertEqual(row['field_status']['favicon'],'upstream_host_mismatch')
        self.assertIsNone(row['author'])

    def test_actual_upstream_modules_are_retained_but_missing_ones_stay_unknown(self):
        features=[{'type':'people_also_ask','items':[{'title':'Why solar?','link':'https://example.org/q'}]},
                  {'type':'ai_summary','text':'Actual upstream summary'},
                  {'type':'related_searches','items':[{'title':'solar research'}]}]
        result=response_envelope({'serp_features':features},
                                 {'text':'solar','lang':'EN','region':'US','start':0,'limit':10,'features':'true'},[])
        self.assertEqual(result['related_questions'][0]['items'][0]['title'],'Why solar?')
        self.assertEqual(result['module_status']['ai_overview'],'upstream_observed')
        self.assertIsNone(result['ads'])
        self.assertEqual(result['module_status']['ads'],'not_implemented')
        self.assertIsNone(result['search_parameters']['device'])

    def test_nested_transport_secrets_cannot_leak_through_features(self):
        result=public_fields({'title':'Safe','headers':{'Authorization':'secret'},'items':[{'access_token':'secret',
                             'link':'https://site.example/a?access_token=secret&article=2'}]})
        self.assertNotIn('secret',json.dumps(result))
        self.assertEqual(result['items'][0]['link'],'https://site.example/a?article=2')

    def test_rss_enrichment_preserves_unknown_original_link_and_traffic_semantics(self):
        news=parse_feed(NEWS,'google_news','q','US:en')['news_results'][0]
        self.assertEqual(news['publisher_host'],'publisher.example')
        self.assertFalse(news['is_resolved']);self.assertIsNone(news['publisher_link'])
        self.assertEqual(news['field_status']['publisher_link'],'unresolved_google_redirect')
        topic=parse_feed(TRENDS,'google_trends','ignored','US')['trending_searches'][0]
        self.assertEqual(topic['traffic_value'],20000);self.assertTrue(topic['traffic_is_lower_bound'])
        self.assertEqual(topic['geo'],'US')
        self.assertEqual(traffic_fields('1.5M+')['traffic_value'],1500000)
        self.assertIsNone(traffic_fields('unknown')['traffic_value'])

    def test_human_csv_exports_structured_fields_instead_of_dropping_them(self):
        row={'source':'google_web','data':{'title':'A','link':'https://example.org/a',
             'sitelinks':[{'title':'B','url':'https://example.org/b'}],'author':'Jane',
             'field_status':{'author':'observed'},'upstream_domain':'google.com'}}
        out=list(user_result_rows(row))[0]
        self.assertEqual(out['作者'],'Jane')
        self.assertEqual(json.loads(csv_value(out['站内链接']))[0]['title'],'B')
        self.assertEqual(out['上游域名'],'google.com')


if __name__=='__main__':unittest.main()
