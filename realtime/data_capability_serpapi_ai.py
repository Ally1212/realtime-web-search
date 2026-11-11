"""Explicitly enabled SerpApi Google AI Mode candidate; never AIO or model API.

Official protocol and acceptance limits: docs/data-capability-serpapi-ai-2026-11-10.md.
No credential discovery, outbound call, service wiring or initialization on import.
"""
from __future__ import annotations

import copy
from datetime import datetime

from .data_capability_contract import validate_request
from .data_capability_facts import VERSION, validate_result, http_url
from .data_capability_serpapi import SerpAPITrendsAdapter, UTC
from .product import ProductError

PARSER = 'serpapi-ai-mode-1'
SUPPORTED_TYPES = {'heading','paragraph','list','table','code_block','expandable','comparison','top_stories'}
ROOT_FIELDS = {'search_metadata','search_parameters','text_blocks','references','reconstructed_markdown',
               'inline_images','inline_products','quick_results','shopping_results','local_results','inline_videos',
               'related_questions','subsequent_request_token','is_partial','is_truncated','has_more','serpapi_pagination'}
MAX_ITEMS = 100000


def request_parameters(payload):
    p=validate_request(payload)
    if p['capability']!='ai_mode_answer' or p['language']!='en' or p['country'] not in ('US','GB'):
        raise ProductError('unsupported_parameter','此AI Mode候选支持US/GB、en、桌面或手机的单轮文本问题；AIO另行接入',409)
    return dict(engine='google_ai_mode',q=p['prompt'],gl='uk' if p['country']=='GB' else 'us',hl=p['language'],
                device=p['device'],no_cache='true',continuable='false',**{'async':'false','output':'json'})


def scope_verified(response, expected):
    echo=response.get('search_parameters')
    return isinstance(echo,dict) and all(echo.get(k)==expected[k] for k in ('engine','q','gl','hl','device'))


def _array(value):
    if not isinstance(value,list) or len(value)>MAX_ITEMS:raise ValueError('invalid bounded source list')
    return value


def _text(value):
    if not isinstance(value,str) or '\x00' in value:raise ValueError('invalid source text')
    return value


def normalize(payload, response, *, requested_at, received_at, retain_raw=False, redacted_paths=()):
    p=validate_request(payload);sent=request_parameters(p)
    if not isinstance(response,dict) or not scope_verified(response,sent) or 'ai_overview' in response or 'organic_results' in response:
        raise ValueError('AI Mode response channel or effective parameters disagree')
    return normalize_scoped_answer(p,response,requested_at=requested_at,received_at=received_at,
        retain_raw=retain_raw,redacted_paths=redacted_paths)


def normalize_scoped_answer(p,response,*,requested_at,received_at,retain_raw=False,redacted_paths=(),
                            channel='ai_mode',api_version='unversioned-google-ai-mode',parser=PARSER,
                            termination='serpapi_synchronous_ai_mode_response',complete_reason='synchronous_google_ai_mode_success_no_answer_pagination'):
    """Shared structure parser. Caller must have verified the native channel/scope."""
    meta=response.get('search_metadata',{})
    if meta.get('status')!='Success' or not isinstance(meta.get('id'),str) or not meta['id']:
        raise ValueError('source did not confirm a successful response identity')
    created=datetime.strptime(meta['created_at'],'%Y-%m-%d %H:%M:%S UTC').replace(tzinfo=UTC)
    if not requested_at.replace(microsecond=0)<=created<=received_at:
        raise ValueError('source response is stale or future')
    if meta.get('cached',False) is not False or response.get('cached',False) is not False:
        raise ValueError('unexpected cached source response')
    blocks_in=_array(response['text_blocks']);references=_array(response.get('references',[]))
    # Missing references is permitted only if no source block references an index.
    refs={};citations=[]
    for pos,r in enumerate(references):
        if not isinstance(r,dict) or type(r.get('index')) is not int or r['index']<0 or r['index'] in refs:
            raise ValueError('reference identities must be explicit and unique')
        http_url(r.get('link'))
        if r.get('title') is not None and not _text(r['title']).strip():raise ValueError('invalid reference title')
        item=dict(id='reference-'+str(r['index']),url=r['link'],title=r.get('title'),role='citation',
                  source_ref='/references/'+str(pos),mapping_status='not_covered',block_ids=[],text_ranges=[],
                  source_fields=dict(source_reference=copy.deepcopy(r),mapping_paths=[]))
        refs[r['index']]=item;citations.append(item)
    warnings=['answer_text_is_ordered_source_text_join_not_provider_reconstructed_markdown',
              'gl_is_country_localization_not_verified_physical_search_origin']
    partial=bool(set(response)-ROOT_FIELDS) or (bool(blocks_in) and 'references' not in response)
    if partial:warnings.append('unrecognized_top_level_fields_preserved_only_if_raw_retention_allowed')
    truncated=False
    for container in [response,meta]:
        for key in ('is_partial','is_truncated','has_more'):
            if key in container and type(container[key]) is not bool:raise ValueError('invalid source completeness flag')
            if container.get(key):partial=True
        truncated=truncated or container.get('is_truncated',False)
    if response.get('serpapi_pagination') or any(path in ('/next_page_token','/page_token','/pagination_token') for path in redacted_paths):
        partial=True;warnings.append('source_continuation_not_fetched')
    links=[];blocks=[];nodes=0;unsupported=False
    def add_link(value,title,path,fields):
        http_url(value)
        if title is not None and not _text(title).strip():raise ValueError('invalid ordinary link title')
        links.append(dict(url=value,title=title,source_fields=dict(source_ref=path,source_record=copy.deepcopy(fields))))
        if len(links)>MAX_ITEMS:raise ValueError('too many source links')
    def walk(node,path,bid,depth=0):
        nonlocal nodes,partial,unsupported,truncated
        nodes+=1
        if nodes>MAX_ITEMS or depth>32:raise ValueError('source answer structure exceeds bounds')
        if not isinstance(node,dict):raise ValueError('invalid structured answer node')
        parts=[]
        for flag in ('is_partial','is_truncated','has_more'):
            if flag in node and type(node[flag]) is not bool:raise ValueError('invalid nested completeness flag')
            if node.get(flag):partial=True
        truncated=truncated or node.get('is_truncated',False)
        if 'type' in node:
            kind=_text(node['type'])
            if kind not in SUPPORTED_TYPES:partial=True;unsupported=True
        for key in ('title','subtitle','snippet','code'):
            if key in node:parts.append(_text(node[key]))
        for key in ('link','product_link'):
            if key in node:add_link(node[key],node.get('title'),path+'/'+key,node)
        if 'video' in node:
            video=node['video']
            if not isinstance(video,dict):raise ValueError('invalid source video')
            if 'link' in video:add_link(video['link'],video.get('title'),path+'/video/link',video)
        if 'table' in node:
            for row in _array(node['table']):
                parts.append('\t'.join(_text(cell) for cell in _array(row)))
        if 'reference_indexes' in node:
            indexes=_array(node['reference_indexes'])
            if len(set(indexes))!=len(indexes):raise ValueError('duplicate reference mapping')
            for index in indexes:
                if type(index) is not int or index not in refs:raise ValueError('unresolved source reference index')
                r=refs[index];r['mapping_status']='source_explicit'
                if bid not in r['block_ids']:r['block_ids'].append(bid)
                r['source_fields']['mapping_paths'].append(path+'/reference_indexes')
        if 'snippet_links' in node:
            inline=node['snippet_links']
            for n,link in enumerate([inline] if isinstance(inline,dict) else _array(inline)):
                if not isinstance(link,dict):raise ValueError('invalid inline link')
                add_link(link.get('link'),link.get('text'),path+'/snippet_links'+('' if isinstance(inline,dict) else '/'+str(n)),link)
        # Preserve nested list/text/code structures and source relations to the
        # containing top-level block; never guess character spans or array IDs.
        for key in ('list','text_blocks','top_stories'):
            if key in node:
                for n,child in enumerate(_array(node[key])):
                    parts.append(walk(child,path+'/'+key+'/'+str(n),bid,depth+1))
        for key in ('code_block','shopping_result','local_result'):
            if key in node:
                child=node[key]
                parts.append(walk(child,path+'/'+key,bid,depth+1))
        if 'comparison' in node:
            if 'product_labels' in node:parts.append('\t'.join(_text(v) for v in _array(node['product_labels'])))
            for row in _array(node['comparison']):
                if not isinstance(row,dict) or set(row)!={'feature','values'}:raise ValueError('invalid source comparison row')
                parts.append('\t'.join([_text(row['feature'])]+[_text(v) for v in _array(row['values'])]))
        if 'detailed' in node and 'table' not in node:partial=True;unsupported=True
        # Unknown structure survives in `structure`, but not as a claim of full
        # rendered text. Known decoration is retained without duplicating text.
        known={'is_partial','is_truncated','has_more','type','title','subtitle','snippet','code','language','table','formatted','detailed','reference_indexes','snippet_links',
               'snippet_highlighted_words','snippet_inline_code','list','text_blocks','code_block','shopping_result','local_result',
               'thumbnail','link','product_link','source','displayed_link','favicon','rating','reviews','price','extracted_price',
               'old_price','extracted_old_price','data_cid','data_cid_search','gps_coordinates','provider_id','address','open_state',
               'serpapi_immersive_product_api','serpapi_product_api','snippet_latex','video','top_stories','source_icon','live','date','comparison','product_labels'}
        if set(node)-known:partial=True;unsupported=True
        return '\n'.join(parts)
    for pos,node in enumerate(blocks_in):
        if not isinstance(node,dict) or not isinstance(node.get('type'),str) or not node['type'].strip():
            raise ValueError('missing source block type')
        bid='block-'+str(pos);text=walk(node,'/text_blocks/'+str(pos),bid)
        blocks.append(dict(id=bid,position=pos,type=node['type'],text=text,structure=copy.deepcopy(node),
                           source_fields=dict(source_ref='/text_blocks/'+str(pos))))
    for key in ('quick_results','shopping_results','local_results','inline_videos','inline_products'):
        for n,item in enumerate(_array(response.get(key,[]))):
            if not isinstance(item,dict):raise ValueError('invalid ancillary result')
            for field in ('link','product_link'):
                if field in item:add_link(item[field],item.get('title'),'/'+key+'/'+str(n)+'/'+field,item)
    for n,url in enumerate(_array(response.get('inline_images',[]))):add_link(url,None,'/inline_images/'+str(n),dict(role='inline_image'))
    text='\n\n'.join(b['text'] for b in blocks)
    if blocks and not text.strip():raise ValueError('source blocks have no verified textual representation')
    if not blocks and (partial or citations or response.get('reconstructed_markdown')):
        raise ValueError('empty blocks conflict with other answer evidence')
    if unsupported:warnings.append('unrecognized_block_content_retained_as_structure_not_complete_text')
    if truncated:warnings.append('source_explicitly_truncated')
    if not blocks:
        state='not_observed';text=None
    else:state='partial' if partial else 'complete'
    raw=dict(status='not_retained',payload=None,redacted_paths=[],reason='adapter_raw_retention_not_enabled')
    if retain_raw:
        raw=dict(status='redacted' if redacted_paths else 'stored',payload=response,redacted_paths=list(redacted_paths),
                 reason='source_credentials_or_short_lived_tokens_removed' if redacted_paths else None)
    reconstructed=response.get('reconstructed_markdown')
    if reconstructed is not None:_text(reconstructed)
    answer=dict(id=meta['id'],channel=channel,state=state,answer_text=text,blocks=blocks,citations=citations,links=links,model=None,
                expansion=dict(state='partial' if partial else 'not_required',
                    end_reason='source_response_incomplete_or_rendering_unverified' if partial else complete_reason),
                source_fields=dict(provider_created_at=created.isoformat(),text_basis='ordered_source_title_snippet_code_table_cells_with_explicit_separators',
                    block_separator='\n\n',nested_separator='\n',table_cell_separator='\t',provider_reconstructed_markdown=reconstructed,
                    model_unknown_reason='source_did_not_declare_a_verified_model',reference_count=len(citations),
                    related_questions_count=len(_array(response.get('related_questions',[])))))
    # Empty observed answer has no unfinished expansion to assert. Unknown
    # structures without text already fail rather than inventing a success.
    if state=='not_observed':answer['expansion']=dict(state='not_required',end_reason='source_explicit_empty_text_blocks')
    return validate_result(p,dict(schema_version=VERSION,source=dict(provider='serpapi',api_version=api_version,
        parser_version=parser,source_request_id=meta['id'],source_observed_at=None,
        unknown_time_reason='provider_request_creation_is_not_an_independent_google_observation_time'),
        requested_at=requested_at.isoformat(),received_at=received_at.isoformat(),effective_parameters=p,is_partial=partial,
        is_truncated=truncated,termination_reason=termination,warnings=warnings,raw=raw,data=dict(answer=answer)))


class SerpAPIAIModeAdapter(SerpAPITrendsAdapter):
    request_parameters=staticmethod(request_parameters)
    scope_verified=staticmethod(scope_verified)
    normalize=staticmethod(normalize)

    def __repr__(self):return '<SerpAPIAIModeAdapter credential=redacted enabled='+str(self.enabled)+'>'

    def validate_parameters(self,payload):
        if not self.enabled:raise ProductError('source_not_admitted','SerpApi AI Mode候选尚未启用',409)
        self.request_parameters(payload)
