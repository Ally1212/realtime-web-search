import re
import unittest
from realtime.insights_core import prepare_records,observed_summary
from realtime.insights_embed import components,extend

class EmbedTests(unittest.TestCase):
 def test_scoped_content_and_shared_shell(self):
  section,js=components()
  ids=re.findall(r'id="([^"]+)"',section)
  self.assertEqual(len(ids),len(set(ids)))
  self.assertTrue(all(x=='insightsView' or x.startswith('ti-') for x in ids))
  referenced={'ti-'+x for x in re.findall(r"\$\('([^']+)'\)",js)}
  self.assertFalse(referenced-set(ids),referenced-set(ids))
  self.assertNotIn('<aside',section)
  self.assertNotIn('<iframe',section)
  self.assertNotIn('function showView',js)
  self.assertIn("showView=function()",js)
  self.assertIn('renderObserved()',js)
  self.assertIn('root.hidden||!current',js)
  self.assertIn('确认标签并分析',section)
 def test_extension_idempotent_and_in_page_navigation(self):
  page='<style></style><a href="/insights">文本洞察</a><main><footer class="foot" id="updated"></footer></main><script>window.addEventListener(\'hashchange\',showView);showView();</script>'
  result=extend(page)
  self.assertIn('href="#insights" data-view="insights"',result)
  self.assertNotIn('href="/insights"',result)
  self.assertEqual(extend(result),result)
 def test_statistics_only_use_stored_fields_and_skip_google_wrappers(self):
  rows=[dict(text_raw='Title\nSnippet',title='Title',snippet='Snippet',query='cats',collected_at='2026-11-10T00:00:00Z',url='https://www.example.com/a'),dict(text_raw='other',url='https://news.google.com/rss/articles/a')]
  records,_=prepare_records(rows,'search');d=observed_summary(records)
  self.assertEqual(d['unique_domains'],1)
  self.assertEqual(d['with_snippet'],1)
  self.assertEqual(d['queries'],[{'name':'cats','count':1}])
  self.assertIsNone(d['records'][1]['collected_at'])
  self.assertIsNone(d['records'][1]['title'])
