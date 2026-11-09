import base64
from copy import deepcopy
import json
from unittest.mock import Mock

import pytest
from realtime.google_news_links import (article_id,offline_target,parameters,rpc_target,NewsLinkResolver,enrich_feed,RPC)

TOKEN='ARTICLE_TOKEN_12345'
WRAPPER='https://news.google.com/rss/articles/'+TOKEN
TARGET='https://publisher.example/article?a=1'


def reply(body=b'',status=200,headers=None):
    r=Mock(status_code=status,headers=headers or {})
    r.iter_content.return_value=[body]
    return r


def discovery():
    d=Mock(task_options={},should_continue=lambda:True,news_link_resolver=None)
    d._external_get.return_value=reply(f'<div data-n-a-id="{TOKEN}" data-n-a-ts="123" data-n-a-sg="public-signature"></div>'.encode())
    payload=json.dumps([['wrb.fr','Fbv4je',json.dumps(['garturlres',TARGET,1])]]).encode()
    d._external_post.return_value=reply(b")]}'\n\n"+payload)
    return d


def store():
    s=Mock();s.get_discovery_cache.return_value=None;s.acquire_query_lease.return_value='lease';s.acquire_discovery_slot.return_value={'allowed':True,'wait':0}
    return s


def test_protocol_framing_and_wrong_rpc_rejected():
    body=json.dumps([['wrb.fr','Fbv4je',json.dumps(['garturlres',TARGET,1])]])
    assert rpc_target(")]}'\n\n"+str(len(body))+'\n'+body)==TARGET
    with pytest.raises(ValueError):rpc_target(json.dumps([['wrb.fr','wrong',json.dumps(['garturlres',TARGET])]]))
    for target in ('http://127.0.0.1/a','https://news.google.com/read/x','javascript:bad','https://example.com/?access_token=secret'):
        with pytest.raises(ValueError):rpc_target(json.dumps([['wrb.fr','Fbv4je',json.dumps(['garturlres',target])]]))


def test_protobuf_url_uses_declared_length_not_trailing_binary():
    value=TARGET.encode();raw=b'\x08\x13\x22'+bytes([len(value)])+value+b'\xd2\x01\x00'
    token=base64.urlsafe_b64encode(raw).decode().rstrip('=')
    assert offline_target('https://news.google.com/rss/articles/'+token)==TARGET
    assert article_id('https://news.google.com.evil.example/rss/articles/'+token) is None
    assert article_id('https://user:pass@news.google.com/articles/'+TOKEN) is None
    with pytest.raises(ValueError):parameters(b'<div data-n-a-id="other" data-n-a-ts="123" data-n-a-sg="sig"></div>',TOKEN)


def test_resolves_rpc_and_never_follows_publisher_or_retains_signature():
    d,s=discovery(),store();r=NewsLinkResolver(d,s,'public').resolve(WRAPPER)
    assert r['status']=='resolved' and r['target']==TARGET and r['http_request_count']==2
    assert 'public-signature' not in json.dumps(r)
    assert d._external_get.call_count==1 and d._external_post.call_count==1
    assert d._external_post.call_args.args==(RPC,)
    assert d._external_get.call_args.kwargs['allow_redirects'] is False
    assert d._external_post.call_args.kwargs['allow_redirects'] is False
    assert s.record_discovery_result.call_count==2
    s.release_query_lease.assert_called_once()


def test_google_redirect_yields_target_without_requesting_target():
    d,s=discovery(),store();d._external_get.return_value=reply(status=302,headers={'Location':TARGET})
    r=NewsLinkResolver(d,s,'public').resolve(WRAPPER)
    assert r['target']==TARGET and r['method']=='google_http_redirect'
    d._external_post.assert_not_called();d._external_get.assert_called_once()


def test_shared_cooling_stop_and_deadline_never_make_network_calls():
    d,s=discovery(),store();s.acquire_discovery_slot.return_value={'allowed':False,'wait':1800}
    r=NewsLinkResolver(d,s,'public').resolve(WRAPPER)
    assert r['status']=='deferred' and r['reason']=='resolution_source_cooling'
    d._external_get.assert_not_called()
    r=NewsLinkResolver(d,store(),'public',budget_seconds=0).resolve(WRAPPER)
    assert r['reason']=='resolution_batch_deadline'
    d.should_continue=lambda:False
    r=NewsLinkResolver(d,store(),'public').resolve(WRAPPER)
    assert r['reason']=='task_not_runnable'


def test_429_is_cached_and_recorded_without_rpc_or_retry():
    d,s=discovery(),store();d._external_get.return_value=reply(status=429)
    r=NewsLinkResolver(d,s,'public').resolve(WRAPPER)
    assert r['status']=='deferred' and r['reason']=='google_http_429'
    d._external_post.assert_not_called()
    assert s.put_discovery_cache.call_args.args[-1]==1800
    assert s.record_discovery_result.call_args.kwargs['captcha'] is True


def test_cached_and_private_scopes_do_not_mix_and_invalid_cache_ignored():
    d,s=discovery(),store();s.get_discovery_cache.return_value={'payload':[{'adapter_version':'google-news-links-1','wrapper':WRAPPER,'status':'resolved','target':TARGET,'observed_at':'2026-11-09T00:00:00Z'}]}
    r=NewsLinkResolver(d,s,'a').resolve(WRAPPER)
    assert r['cache_hit'] and r['http_request_count']==0
    d._external_get.assert_not_called();public_key=s.get_discovery_cache.call_args.args[0]
    d.task_options={'customer_private':True};NewsLinkResolver(d,s,'a').resolve(WRAPPER);private_a=s.get_discovery_cache.call_args.args[0]
    NewsLinkResolver(d,s,'b').resolve(WRAPPER);private_b=s.get_discovery_cache.call_args.args[0]
    assert len({public_key,private_a,private_b})==3
    s.get_discovery_cache.return_value={'payload':[{'adapter_version':'google-news-links-1','wrapper':WRAPPER,'status':'resolved','target':'http://localhost/'}]}
    assert NewsLinkResolver(d,s,'a').resolve(WRAPPER)['target']==TARGET


def test_enrichment_preserves_rss_facts_and_duplicate_occurrences():
    from realtime.google_feeds import parse_feed
    from tests.test_google_feeds import NEWS
    response=parse_feed(NEWS.replace(b'opaque-token',TOKEN.encode()),'google_news','q','US:en')
    row=deepcopy(response['news_results'][0]);response['news_results'].append(deepcopy(row))
    d,s=discovery(),store()
    summary=enrich_feed(response,d,s,'a')
    assert summary['resolved']==2 and len(response['news_results'])==2
    for enriched in response['news_results']:
        assert enriched['link']==TARGET and enriched['publisher_link']==TARGET
        assert enriched['original_link']==WRAPPER and enriched['google_news_link']==WRAPPER
        assert enriched['date']==row['date'] and enriched['title']==row['title']
        assert enriched['publisher']==row['publisher'] and enriched['snippet']==row['snippet']
    assert summary['publisher_fetches']==0


def test_queue_wait_beyond_budget_does_not_reserve_future_slots_for_every_link():
    d,s=discovery(),store();s.acquire_discovery_slot.return_value={'allowed':True,'wait':200}
    resolver=NewsLinkResolver(d,s,'public',budget_seconds=1)
    assert resolver.resolve(WRAPPER)['reason']=='resolution_batch_deadline'
    assert resolver.resolve(WRAPPER+'more')['reason']=='resolution_batch_deadline'
    assert s.acquire_discovery_slot.call_count==1
    d._external_get.assert_not_called()


def test_unsupported_google_wrapper_is_explicitly_incomplete_not_silently_complete():
    d,s=discovery(),store();response={'news_results':[{'link':'https://news.google.com/new-format/unknown'}]}
    summary=enrich_feed(response,d,s,'a')
    assert summary['eligible']==1 and summary['unresolved']==1 and not summary['complete']
    assert response['news_results'][0]['link_resolution']['reason']=='unsupported_google_news_url'
    d._external_get.assert_not_called()


class TestNewsAliases:
    @pytest.fixture
    def dbstore(self):
        import os,psycopg
        from psycopg import sql
        from psycopg.conninfo import make_conninfo
        from uuid import uuid4
        from realtime.campaign_store import CampaignStore
        from realtime.serp_store import SerpStore
        root=os.getenv('TEST_DATABASE_URL')
        if not root:pytest.skip('requires isolated PostgreSQL')
        schema='news_alias_'+uuid4().hex
        with psycopg.connect(root) as db:db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        dsn=make_conninfo(root,options=f'-c search_path={schema}');base=CampaignStore(dsn);serp=SerpStore(dsn);serp.initialize()
        with base.connect() as db:
            cid=str(uuid4());db.execute("INSERT INTO campaigns(id,query,daily_target,proxy_profile,status,sources) VALUES(%s,'news',1,'direct','active', '[\"google_news\"]')",(cid,))
        yield base,serp,cid
        base.pool.close()
        with psycopg.connect(root) as db:db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))

    def record(self,serp,cid,link,proof=None):
        item=dict(link=link,title='saved title',publisher='Publisher')
        if proof:item['link_resolution']=proof
        return serp.record(cid,source='google_news',query='news',page=1,provider='google_rss',language='en',classification='results',http_status=200,response={'news_results':[item]},elapsed_seconds=.1)

    def test_historical_wrapper_resolution_does_not_count_a_second_new_url(self,dbstore):
        import hashlib
        base,serp,cid=dbstore
        old=self.record(serp,cid,WRAPPER);original=deepcopy(serp.request(old))
        proof=dict(adapter_version='google-news-links-1',status='resolved',method='google_news_rpc',wrapper=WRAPPER,target=TARGET,publisher_fetched=False)
        new=self.record(serp,cid,TARGET,proof)
        assert serp.request(new)['new_unique_links']==0
        assert serp.request(old)==original
        with base.connect() as db:
            rows=db.execute('SELECT * FROM serp_urls ORDER BY normalized_url').fetchall()
            assert len(rows)==2
            oldrow=next(r for r in rows if r['normalized_url']==WRAPPER)
            assert oldrow['canonical_url_hash']==hashlib.sha256(TARGET.encode()).hexdigest()
            assert db.execute('SELECT count(*) n FROM serp_urls WHERE NOT historical AND canonical_url_hash IS NULL').fetchone()['n']==1
            assert db.execute('SELECT first_request_id FROM serp_urls WHERE url_hash=%s',(oldrow['canonical_url_hash'],)).fetchone()['first_request_id']==__import__('uuid').UUID(old)

    def test_brand_new_target_counts_once_and_concurrent_resolution_is_safe(self,dbstore):
        from concurrent.futures import ThreadPoolExecutor
        base,serp,cid=dbstore
        self.record(serp,cid,WRAPPER)
        proof=dict(adapter_version='google-news-links-1',status='resolved',method='google_news_rpc',wrapper=WRAPPER,target=TARGET)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=[f.result() for f in [pool.submit(self.record,serp,cid,TARGET,proof) for _ in range(2)]]
        assert sum(serp.request(r)['new_unique_links'] for r in results)==0
        another='https://publisher.example/new-article'
        rid=self.record(serp,cid,another)
        assert serp.request(rid)['new_unique_links']==1

    def test_new_feed_pipeline_saves_publisher_link_and_exports_same_contract(self,dbstore,tmp_path):
        from realtime.discovery import SearchDiscovery
        from realtime.google_feeds import collect
        from realtime.serp_console import ConsoleStore
        from realtime.config import Config
        from realtime.google_legacy import read_public_request
        from realtime.google_data_export import export_public_data,verify_bundle
        from tests.test_google_feeds import NEWS
        base,serp,cid=dbstore
        # Use the DSN on the actual store, avoiding any legacy migration at reads.
        dsn=serp.store.dsn
        ConsoleStore(dsn).initialize_console(Config(database_url=dsn))
        d=SearchDiscovery(proxy_profile='direct');d.task_options={};d.news_link_resolution=True
        d._external_get=Mock(side_effect=[reply(NEWS.replace(b'opaque-token',TOKEN.encode())),reply(f'<div data-n-a-id="{TOKEN}" data-n-a-ts="123" data-n-a-sg="sig"></div>'.encode())])
        d._external_post=Mock(return_value=reply(json.dumps([['wrb.fr','Fbv4je',json.dumps(['garturlres',TARGET,1])]]).encode()))
        rows,errors=collect(d,base,serp,cid,'google_news','q','US:en',3600)
        assert not errors and rows[0].url==TARGET
        snapshot=serp.recent(cid)[0]
        saved=serp.request(snapshot['id'])
        assert saved['feed_retrieval']['elapsed_seconds'] < saved['link_resolution_summary']['elapsed_seconds']
        assert saved['feed_retrieval']['received_at'] <= saved['news_results'][0]['link_resolution']['observed_at']
        with base.connect() as db:
            rid=str(db.execute('SELECT request_id FROM serp_requests').fetchone()['request_id'])
        value=read_public_request(dsn,rid,normalized=True,field_contract='prd-v1')['request']
        assert value['field_adaptation']['items'][0]['publisher_article_url']==TARGET
        directory=tmp_path/'bundle';export_public_data(dsn,directory,filters={'field_contract':'prd-v1'})
        assert verify_bundle(directory)['requests']==1
        exported=json.loads((directory/'requests.jsonl').read_text())
        assert exported==value
        d.close()


def test_singleflight_rechecks_cache_after_acquiring_lease():
    d,s=discovery(),store()
    saved={'adapter_version':'google-news-links-1','wrapper':WRAPPER,'status':'resolved','target':TARGET,'method':'google_news_rpc'}
    s.get_discovery_cache.side_effect=[None,{'payload':[saved]}]
    result=NewsLinkResolver(d,s,'a').resolve(WRAPPER)
    assert result['cache_hit'] and result['http_request_count']==0
    d._external_get.assert_not_called();s.release_query_lease.assert_called_once()


def test_untrusted_google_redirect_never_follows_another_article_or_publisher():
    d,s=discovery(),store();d._external_get.return_value=reply(status=302,headers={'Location':'https://news.google.com/articles/OTHER_ARTICLE_1234'})
    result=NewsLinkResolver(d,s,'a').resolve(WRAPPER)
    assert result['status']=='unresolved' and result['reason']=='google_redirect_not_allowed'
    assert result['target'] is None and d._external_get.call_count==1
    d._external_post.assert_not_called()


def test_oversized_google_response_is_closed_and_keeps_link_unresolved():
    from realtime.google_news_links import MAX_HTML_BYTES
    d,s=discovery(),store();response=reply(b'x'*(MAX_HTML_BYTES+1));d._external_get.return_value=response
    result=NewsLinkResolver(d,s,'a').resolve(WRAPPER)
    assert result['reason']=='google_resolution_response_too_large' and result['target'] is None
    response.close.assert_called_once();s.release_query_lease.assert_called_once()


def test_cache_storage_failure_propagates_and_releases_resolution_lease():
    d,s=discovery(),store();s.put_discovery_cache.side_effect=RuntimeError('cache database failure')
    with pytest.raises(RuntimeError,match='cache database failure'):NewsLinkResolver(d,s,'a').resolve(WRAPPER)
    s.release_query_lease.assert_called_once()


def test_resolution_failure_preserves_rss_item_and_marks_feed_incomplete():
    from realtime.google_feeds import parse_feed
    from tests.test_google_feeds import NEWS
    response=parse_feed(NEWS.replace(b'opaque-token',TOKEN.encode()),'google_news','q','US:en')
    original=deepcopy(response['news_results'][0]);d,s=discovery(),store();d._external_get.return_value=reply(status=500)
    summary=enrich_feed(response,d,s,'a');item=response['news_results'][0]
    assert not summary['complete'] and summary['unresolved']==1
    for field in ('title','link','snippet','publisher','date','original_link'):
        assert item[field]==original[field]
    assert item['publisher_link'] is None and item['is_resolved'] is False
