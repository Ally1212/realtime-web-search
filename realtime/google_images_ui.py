"""Images reuse the workbench controls and result cards, with opt-in previews."""
import re

JS = r'''
const imageWebEntry=document.querySelector('[data-open-source="google_web"]');if(imageWebEntry){const entry=imageWebEntry.cloneNode(true);entry.dataset.openSource='google_images';entry.querySelector('strong').textContent='Google Images';entry.querySelector('small').textContent='图片外链 · 来源网页与上游尺寸';imageWebEntry.parentElement.append(entry);}
const imageBaseResult=renderResult;
renderResult=function(x){if(x.source!=='google_images')return imageBaseResult(x);const a=x.data||{},key=rememberRecord(x);return `<article class="result images"><div class="result-meta"><span class="badge">Google Images</span><span>采集于 ${time(x.completed_at)}</span><span>关键词：${esc(x.query)}</span>${a.is_new_url?'<span class="badge ok">首次发现图片 URL</span>':''}</div><h3>${esc(a.title||'标题未提供')}</h3><div class="record-facts"><span><small>图片尺寸</small>${a.width&&a.height?esc(a.width+' × '+a.height):'上游未提供'}</span><span><small>本批返回顺序</small>${esc(a.observed_order??'未知')}</span><span><small>来源网站</small>${esc(a.source_host||'未知')}</span></div><p><a href="${safeLink(a.image_url)}" target="_blank" rel="noopener noreferrer">打开图片链接</a>${a.source_url?` · <a href="${safeLink(a.source_url)}" target="_blank" rel="noopener noreferrer">查看来源网页</a>`:''}</p><div class="actions">${a.thumbnail&&safeLink(a.thumbnail)!=='#'?`<button type="button" class="mini secondary" data-image-preview="${esc(a.thumbnail)}">加载缩略图</button>`:''}<button type="button" class="mini secondary" data-saved="${esc(key)}">查看已保存数据</button></div><p class="help">仅保存图片搜索信息；未下载原图。返回顺序不是网页自然排名。${a.thumbnail?'':'上游未提供缩略图，可通过图片链接查看。'}</p></article>`;};
document.addEventListener('click',e=>{const b=e.target.closest('[data-image-preview]');if(!b)return;const src=safeLink(b.dataset.imagePreview);if(src==='#')return;const img=document.createElement('img');img.alt='已保存结果的缩略图';img.loading='lazy';img.referrerPolicy='no-referrer';img.className='image-result-preview';img.onload=()=>b.remove();img.onerror=()=>{img.remove();b.disabled=true;b.textContent='预览不可用，原始数据仍保留';};b.disabled=true;b.textContent='正在读取预览…';b.parentElement.after(img);img.src=src;});
if(typeof grHistoryItem==='function'){const imageBaseHistory=grHistoryItem;grHistoryItem=function(a){if(a.kind!=='image')return imageBaseHistory(a);return `<div class="gr-history-item"><strong>${esc(a.title||'标题未提供')}</strong><p><a href="${safeLink(a.url)}" target="_blank" rel="noopener noreferrer">图片链接</a>${a.source_page_url?` · <a href="${safeLink(a.source_page_url)}" target="_blank" rel="noopener noreferrer">来源网页</a>`:''}</p><p>${a.width&&a.height?esc(a.width+' × '+a.height):'尺寸未知'} · 返回顺序 ${esc(a.observed_order??'未知')} · 未下载原图</p></div>`;};}
const imageBaseFields=savedFieldRows;
savedFieldRows=function(x){if(x.source!=='google_images')return imageBaseFields(x);const a=x.data||{};return [['标题',a.title,'上游提供的图片结果标题。'],['图片地址',a.image_url,'图片文件外链；未下载原图。'],['来源网页',a.source_url,'发布图片的网页，与图片文件地址不同。'],['缩略图地址',a.thumbnail,'可选预览，可能过期。'],['宽度',a.width,'上游提供的像素宽度；未下载验证。'],['高度',a.height,'上游提供的像素高度；未下载验证。'],['本批返回顺序',a.observed_order,'图片网格顺序，不是网页自然排名或全局排名。'],['上游排名',a.position,'只保留上游明确返回的值。'],['关键词',x.query,'本次图片搜索使用的关键词。'],['完成时间',x.completed_at,'采集完成时间，不是图片发布时间。']];};
'''


def extend(page):
    page=page.replace('当前固定首屏 10 条上限，上游可能返回更少。','图片搜索最多请求 20 条；其他来源沿用各自结果范围，实际数量由上游决定。')
    page=page.replace('汇总来自近 90 天公开数据','汇总仅含近 90 天 Web、News、Trends 公开数据；图片请在数据集查看')
    page=page.replace('按实际来源保存与分析。','按实际来源保存与导出。').replace('到“数据集”筛选结果，下载 CSV 供表格使用，或同步到 Whale。','到“数据集”筛选结果，下载 CSV / JSONL。')
    # Add only data/task/API source choices, not analytical or monitoring scopes.
    for ident in ('resultSource','grHistorySource','apiSource'):
        pattern=r'(<select\b[^>]*id="'+ident+r'"[^>]*>)(.*?)(</select>)'
        page=re.sub(pattern,lambda m:m[1]+m[2]+'<option value="google_images">Google Images</option>'+m[3],page,flags=re.S)
    page=page.replace("google_trends:'Google Trends'", "google_trends:'Google Trends',google_images:'Google Images'")
    page=page.replace("google_trends:'当前热门话题及相关报道'", "google_trends:'当前热门话题及相关报道',google_images:'图片外链、来源网页与尺寸'")
    page=page.replace("google_trends:'trending_searches'", "google_trends:'trending_searches',google_images:'image_results'")
    page=page.replace('</style>','.source-entries{grid-template-columns:repeat(auto-fit,minmax(min(100%,200px),1fr))}.image-result-preview{display:block;max-width:100%;max-height:220px;object-fit:contain;margin:12px 0;border-radius:8px}.source-cards{grid-template-columns:repeat(auto-fit,minmax(180px,1fr))}</style>',1)
    anchor="window.addEventListener('hashchange',showView);showView();setInterval(refreshView,10000);"
    assert anchor in page, 'Images UI initialization anchor missing'
    return page.replace(anchor,JS+'\n'+anchor,1)
