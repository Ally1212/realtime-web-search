"""Composition guards for the workbench's string-based extension mechanism."""
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

from realtime.serp_page import PAGE


class Structure(HTMLParser):
    def __init__(self):
        super().__init__(); self.ids=[]; self.nav=[]
    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        if 'id' in a:self.ids.append(a['id'])
        if tag=='a' and 'data-view' in a:self.nav.append(a['data-view'])


def test_actual_page_integrates_new_workspace_without_duplicate_ids_or_lost_legacy_views():
    doc=Structure();doc.feed(PAGE)
    assert len(doc.ids)==len(set(doc.ids))
    for name in ('overview','create','tasks','results','whale','settings','analysis','monitors','api','research','capability'):
        assert name+'View' in doc.ids
        if name!='create':assert name in doc.nav
    assert 'capability' in re.search(r'const productViews=\[(.*?)\]',PAGE).group(1) or "productViews.push('capability')" in PAGE
    for field in re.findall(r"\$\('(dc\w+)'\)",PAGE):
        # These controls are generated only after receiving an authorized record.
        assert field in doc.ids or field in ('dcRoots','dcChildren','dcChildrenMore','dcSavedBatch','dcAckBatch','dcHistoryMore','dcResponse','dcRetryPanel','dcRetrySubmit','dcRetryMaximum','dcRetryConsent','dcRetryExample','dcRetryStatus')
    assert '/v1/data/jobs/preview' in PAGE and '/v1/data/subscriptions?domain=data' in PAGE
    assert '$SEARCHFLOW_ACCESS_KEY' in PAGE and '$SEARCHFLOW_API_KEY' in PAGE


def test_entire_composed_workbench_javascript_is_valid(tmp_path):
    assert shutil.which('node'), 'Node is needed to validate the delivered workbench JavaScript'
    code=tmp_path/'workbench.js';code.write_text('\n'.join(re.findall(r'<script>(.*?)</script>',PAGE,re.S)))
    result=subprocess.run(['node','--check',str(code)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
