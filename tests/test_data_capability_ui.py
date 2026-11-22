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


def test_workspace_keeps_data_flows_and_retires_obsolete_feature_controls():
    doc=Structure();doc.feed(PAGE)
    assert len(doc.ids)==len(set(doc.ids))
    for name in ('overview','create','tasks','results','analysis','monitors','api','research','maps'):
        assert name+'View' in doc.ids
    assert doc.nav == []  # Current React navigation owns the empty host.
    for retired in ('capability','whale','settings'):
        assert retired not in doc.nav
        assert f'<section id="{retired}View" hidden inert aria-hidden="true"></section>' in PAGE
    assert "productViews.push('capability')" not in PAGE
    assert 'id="taskWhaleUpload"' not in PAGE
    assert 'id="dcSubmit"' not in PAGE
    assert 'id="settingsForm"' not in PAGE
    assert 'id="whaleUploadForm"' not in PAGE
    assert 'upload_to_whale:false' in PAGE
    assert '$SEARCHFLOW_ACCESS_KEY' in PAGE and '$SEARCHFLOW_API_KEY' in PAGE


def test_entire_composed_workbench_javascript_is_valid(tmp_path):
    assert shutil.which('node'), 'Node is needed to validate the delivered workbench JavaScript'
    # Script IDs and bundled string literals containing '<script>' must not
    # cause a script to be skipped or mistaken for another HTML element.
    class Scripts(HTMLParser):
        def __init__(self):
            super().__init__(); self.inside=False; self.parts=[]
        def handle_starttag(self, tag, attrs):
            if tag=='script':self.inside=dict(attrs).get('type')!='application/json'
        def handle_data(self, data):
            if self.inside:self.parts.append(data)
        def handle_endtag(self, tag):
            if tag=='script':self.inside=False; self.parts.append('\n')
    scripts=Scripts();scripts.feed(PAGE)
    code=tmp_path/'workbench.js';code.write_text('\n'.join(scripts.parts))
    result=subprocess.run(['node','--check',str(code)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_old_homepage_is_absent_before_any_script_runs():
    for markup in ('class="system-flow"', 'class="access-strip"', 'class="panel workbench-health"', 'class="archive-note"', 'class="brand-mark"', 'Whale 平台', '读取采集状态…', '最近 24 小时采集概况'):
        assert markup not in PAGE
    assert '<html lang="zh-CN" class="sf-booting">' in PAGE
    assert 'id="sfSourceData"' in PAGE
    assert '<div id="sfDataFlow" class="sf-island"></div>' in PAGE
    assert '<div id="overviewArchive" hidden inert aria-hidden="true"></div>' in PAGE
