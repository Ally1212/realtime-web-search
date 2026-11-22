"""Retire obsolete workspace controls while retaining shared data contracts."""
import re
import json
from html import unescape
from html.parser import HTMLParser
from pathlib import Path


def prepare(page: str) -> str:
    # These are contiguous, self-contained client modules in the frozen page.
    start = page.index("productViews.push('capability');")
    end = page.index('(function(){const root=document.getElementById("insightsView");', start)
    page = page[:start] + page[end:]
    start = page.index('let whaleOffset=')
    end = page.index('const refreshing=new Set();', start)
    page = page[:start] + 'async function loadWhale(){}\n' + page[end:]
    start = page.index("$('settingsForm').addEventListener('input'")
    end = page.index('async function loadLatest()', start)
    page = page[:start] + page[end:]
    page = page.replace("upload_to_whale:$('taskWhaleUpload').checked", 'upload_to_whale:false')
    page = re.sub(r'<label class="check"><input type="checkbox" id="taskWhaleUpload">[\s\S]*?</label><p class="help">上传开关关闭时，[\s\S]*?</p>', '', page, count=1)
    page = re.sub(r'<a\b[^>]*data-view="(?:capability|whale|settings|maps)"[^>]*>[\s\S]*?</a>', '', page)
    # Preserve routing shells; the actual forms and actions are absent from HTML.
    spans = []
    stack = []
    for match in re.finditer(r'<section\b[^>]*>|</section>', page):
        if match[0].startswith('</'):
            if not stack:
                continue
            opening, retired = stack.pop()
            if retired:
                spans.append((opening.start(), match.end(), retired))
        else:
            found = re.search(r'id="(capability|whale|settings)View"', match[0])
            stack.append((match, found[1] if found else None))
    assert len(spans) == 3
    for start, end, retired in reversed(spans):
        page = page[:start] + f'<section id="{retired}View" hidden inert aria-hidden="true"></section>' + page[end:]
    page = page.replace('点击左侧 Google Maps。', '选择地图商家模式。')
    return page


class _PageElements(HTMLParser):
    """Locate exact markup spans without reserializing inline business scripts."""
    def __init__(self, page):
        super().__init__(convert_charrefs=False)
        self.page = page
        self.lines = [0]
        for match in re.finditer('\n', page):
            self.lines.append(match.end())
        self.stack, self.nodes = [], []
        self.feed(page)

    def position(self):
        line, col = self.getpos()
        return self.lines[line-1] + col

    def handle_starttag(self, tag, attrs):
        if tag in {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}:
            return
        self.stack.append((tag, dict(attrs), self.position(), self.position()+len(self.get_starttag_text())))

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1][0] == tag:
            name, attrs, start, inner = self.stack.pop()
            self.nodes.append((name, attrs, start, self.position()+len(tag)+3, inner, self.position()))


def remove_legacy_presentation(page: str) -> str:
    """Ship current hosts and inert data bridges, never the rejected homepage."""
    tree = _PageElements(page)
    edits = []
    for tag, attrs, start, end, inner, close in tree.nodes:
        classes = attrs.get('class', '').split()
        replacement = None
        if 'system-flow' in classes:
            replacement = '<div id="sfDataFlow" class="sf-island"></div>'
        elif 'source-entries' in classes:
            sources = []
            subtree = _PageElements(page[inner:close])
            for childtag, a, cstart, cend, ci, cc in subtree.nodes:
                if 'source-entry' not in a.get('class','').split():
                    continue
                content = page[inner:close][ci:cc]
                def text_of(name):
                    match = re.search('<'+name+r'\b[^>]*>([\s\S]*?)</'+name+'>', content)
                    return unescape(re.sub('<[^>]*>', '', match[1])).strip() if match else ''
                logo = re.search(r'<img\b[^>]*src="([^"]+)"', content)
                sources.append(dict(id=a.get('data-open-source','google_maps'),label=text_of('strong').replace('已支持','').strip(),description=text_of('small'),logo=unescape(logo[1]) if logo else None))
            if not any(source['id'] == 'google_images' for source in sources):
                web = next((source for source in sources if source['id']=='google_web'), {})
                sources.append(dict(id='google_images',label='Google Images',description='图片外链 · 来源网页与上游尺寸',logo=web.get('logo')))
            replacement = '<script type="application/json" id="sfSourceData">'+json.dumps(sources,ensure_ascii=False).replace('<',r'\u003c')+'</script><div id="sfSources" class="sf-island"></div>'
        elif attrs.get('id') == 'overviewArchive':
            replacement = '<div id="overviewArchive" hidden inert aria-hidden="true"></div>'
        elif 'workbench-health' in classes:
            replacement = '<div hidden inert aria-hidden="true">'+''.join('<span id="'+key+'"></span>' for key in ['newLinks','attempts','successRate','failures','captchaHint'])+'<button id="refreshLatest" type="button"></button></div>'
        elif tag == 'details' and 'id="sourceStats"' in page[inner:close]:
            replacement = '<div hidden inert aria-hidden="true"><div id="sourceStats"></div><table><tbody id="errorGroups"></tbody><tbody id="recentErrors"></tbody></table></div>'
        elif 'access-strip' in classes:
            replacement = '<span id="workbenchHealth" hidden inert aria-hidden="true"></span>'
        elif 'archive-note' in classes or 'aside-foot' in classes:
            replacement = ''
        elif 'brand' in classes:
            replacement = '<div class="brand"><span class="brand-name">SearchFlow</span></div>'
        elif tag == 'nav' and attrs.get('aria-label') == '主导航':
            replacement = '<nav aria-label="主导航"></nav>'
        if replacement is not None:
            edits.append((start,end,replacement))
    # Ignore child edits when their parent is replaced.
    selected = []
    for edit in sorted(edits, key=lambda e:(e[0],-e[1])):
        if not selected or edit[0] >= selected[-1][1]:
            selected.append(edit)
    for start,end,replacement in reversed(selected):
        page = page[:start]+replacement+page[end:]
    page = page.replace('<div class="source-hub">', '<div id="sfOverviewStats" class="sf-island"></div><div class="source-hub">', 1)
    # The loading state exists before any script executes.
    page = re.sub(r'<html([^>]*)>', lambda m:'<html'+m[1]+' class="sf-booting">',page,count=1)
    return page
