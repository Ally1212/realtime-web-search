"""Self-contained shadcn/ui + AI Elements React islands over the existing SearchFlow workflows.

Business functions retain ownership of fetching, auth, idempotency and downloads.
Only a whitelist of public Maps result fields enters the presentation bridge.
Run `npm ci --prefix frontend && npm --prefix frontend run build` after editing.
"""
from pathlib import Path
import re

ASSETS = Path(__file__).with_name('assets')
BRIDGE = r'''
(()=>{
 'use strict';
 const bridge=window.sfComponentBridge={version:'mono-1',maps:[]};
 const publish=rows=>{
  bridge.maps=rows.map(p=>Object.fromEntries(['name','formatted_address','primary_type','rating','user_rating_count','distance_m','national_phone_number','website_uri','google_maps_uri'].map(k=>[k,p[k]??null])));
  window.dispatchEvent(new Event('sf:maps-results'));
 };
 if(typeof renderMapPlaces==='function'){
  const original=renderMapPlaces;
  renderMapPlaces=function(rows){const html=original(rows);publish(rows);return html;};
 }
})();
'''


def extend(page: str) -> str:
    if 'id="sfComponentStyles"' in page:
        return page
    # Retire the rejected presentation layer, leaving original business scripts.
    page = re.sub(r'<style id="sfObsidianStyles">[\s\S]*?</style>', '', page)
    page = re.sub(r'<script id="sfObsidianScript">[\s\S]*?</script>', '', page)
    from .workspace_refinement import prepare, remove_legacy_presentation
    page = remove_legacy_presentation(prepare(page))
    marker="window.addEventListener('hashchange',showView);showView();setInterval(refreshView,10000);"
    refinement = (ASSETS / 'workspace-refinement.js').read_text()
    assert marker in page
    page = page.replace(marker, refinement+'\n'+marker, 1)
    page = page.replace('</head>', '<script id="sfBootScript">document.documentElement.classList.add("sf-booting")</script></head>', 1)
    skeleton = '<div id="sfBootSkeleton" class="sf-boot-skeleton" role="status" aria-label="正在加载工作台"><div class="sf-skeleton-title"></div><div class="sf-skeleton-flow"></div><div class="sf-skeleton-cards"><i></i><i></i><i></i><i></i></div></div>'
    page = re.sub(r'(<main[^>]*id="mainContent"[^>]*>)', lambda m: m[0]+skeleton, page, count=1)
    css = (ASSETS / 'component-ui.css').read_text()
    js = (ASSETS / 'component-ui.js').read_text()
    assert '</head>' in page and '</body>' in page
    # Escape a closing script in bundled source/comments before HTML embedding.
    js = js.replace('</script', '<\\/script')
    page = page.replace('</head>', '<style id="sfComponentStyles">'+css+'</style></head>', 1)
    return page.replace('</body>', '<script id="sfComponentBridge">'+BRIDGE+'</script><script id="sfComponentScript">'+js+'</script></body>', 1)
