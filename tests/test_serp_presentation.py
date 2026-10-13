import unittest
from realtime.serp_presentation import USER_COLUMNS, user_result_rows, csv_value


class SavedResultPresentationTests(unittest.TestCase):
    def test_final_url_and_unknown_publication_are_not_inferred(self):
        row = {'source':'google_web','query':'中美关系','completed_at':'2026-10-12T14:00:00Z','search_parameters':{'gl':'us','hl':'zh'},'data':{'title':'中美关系研究','link':'https://example.org/report','domain':'google.com','snippet':'2026年9月24日发布的报道','position':2,'date':None,'is_new_url':False}}
        result = list(user_result_rows(row))[0]
        self.assertEqual(result['链接网站'], 'example.org')
        self.assertEqual((result['搜索关键词'],result['搜索地区'],result['搜索语言']),('中美关系','us','zh'))
        self.assertIsNone(result['发布时间'])
        self.assertIsNone(result['媒体'])
        self.assertEqual(result['历史首次发现'],'否')
        self.assertNotIn('json',result)
        self.assertEqual(tuple(result),USER_COLUMNS)

    def test_trends_exports_each_saved_report_and_keeps_linkless_topic(self):
        row = {'source':'google_trends','query':'当前热门趋势','data':{'keyword':'热点','approx_traffic':'20,000+','articles':[{'title':'报道甲','link':'https://example.org/a'},{'title':'报道乙','link':'https://example.org/b'}]}}
        records = list(user_result_rows(row))
        self.assertEqual(len(records),2)
        self.assertIsNone(records[0]['搜索关键词'])
        self.assertEqual(records[1]['热门话题'],'热点')
        row['data']['articles']=[]
        records=list(user_result_rows(row))
        self.assertEqual(len(records),1)
        self.assertIsNone(records[0]['链接'])

    def test_csv_unknown_and_formula_protection(self):
        self.assertEqual(csv_value(None),'未知')
        self.assertEqual(csv_value('=HYPERLINK("bad")'), '\'=HYPERLINK("bad")')
