import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock
from uuid import uuid4

import pytest

from realtime.customer_subscriptions import checksum, batch_file, export_batch
from scripts.pull_data_subscription import pull


def example():
    payload = dict(requests=[dict(request_id=str(uuid4()), occurrences=[])], events=[], request_count=1, event_count=0)
    envelope = dict(batch_id=str(uuid4()), payload=payload, sha256=checksum(payload), checksum_encoding='canonical')
    handler = Mock()
    export_batch(handler, envelope, 'manifest')
    return envelope, handler.send_body.call_args.args[0]


def test_client_verifies_files_before_ack_and_recovers_existing_directory():
    envelope, manifest = example()
    client = Mock()
    paths = []
    def request(method, url, **kwargs):
        paths.append(url)
        response = Mock(status_code=200)
        response.json.return_value = {'acknowledged': True} if url.endswith('/ack') else envelope
        response.content = manifest if url.endswith('manifest') else batch_file(envelope, 'csv' if url.endswith('csv') else 'jsonl')[0]
        return response
    client.request.side_effect = request
    with TemporaryDirectory() as temp:
        args = dict(base='https://example.com', subscription_id=str(uuid4()), output=temp, site_key='site', customer_key='customer', session=client)
        result = pull(**args)
        assert result['verified'] and result['acknowledged']
        assert paths[-1].endswith('/ack')
        assert (Path(result['saved']) / 'manifest.json').exists()
        paths.clear()
        pull(**args)
        assert not any(url.endswith('csv') or url.endswith('jsonl') for url in paths)
        saved = Path(result['saved']) / 'data.csv'
        saved.write_text('corrupt')
        paths.clear()
        with pytest.raises(ValueError, match='checksum mismatch'):
            pull(**args)
        assert not any(url.endswith('/ack') for url in paths)


def test_corrupt_download_never_acknowledges_or_publishes_partial_files():
    envelope, manifest = example()
    client = Mock()
    paths = []
    def request(method, url, **kwargs):
        paths.append(url)
        response = Mock(status_code=200)
        response.json.return_value = envelope
        response.content = manifest if url.endswith('manifest') else b'corrupt'
        return response
    client.request.side_effect = request
    with TemporaryDirectory() as temp:
        with pytest.raises(ValueError, match='checksum mismatch'):
            pull('https://example.com', str(uuid4()), temp, site_key='site', customer_key='customer', session=client)
        assert list(Path(temp).iterdir()) == []
        assert not any(url.endswith('/ack') for url in paths)


def test_subscription_list_uses_existing_table_component_contract():
    import subprocess
    from realtime.product_ui import JS as product_js
    from realtime.subscription_ui import JS as subscription_js
    table = next(line for line in product_js.splitlines() if line.startswith('function productTable('))
    load = next(line for line in subscription_js.splitlines() if line.startswith('async function loadSubscriptions('))
    script = """
const productKey='test', subscriptionBatches=new Map();
let subscriptionRequestVersion=0,subscriptionLoadedKey=null,subscriptionHistoryState=null,subscriptionHistoryVersion=0,subscriptionBatchVersion=0;
const nodes={subscriptionList:{innerHTML:''},subscriptionBatch:{innerHTML:''},subscriptionHistory:{innerHTML:''}};
const $=id=>nodes[id],esc=x=>String(x),productEmpty=x=>x;
async function subscriptionApi(){return {subscriptions:[{id:'id',name:'Saved',filters:{},mode:'new_urls',active:false}]};}
""" + table + '\n' + load + "\nawait loadSubscriptions();if(!nodes.subscriptionList.innerHTML.includes('<tr>')||!nodes.subscriptionList.innerHTML.includes('Saved'))throw Error(nodes.subscriptionList.innerHTML);"
    subprocess.run(['node', '--input-type=module', '-e', script], check=True, capture_output=True)


def test_client_retries_lost_ack_response_without_redownloading_data():
    import requests
    envelope, manifest = example()
    client = Mock()
    count = {'ack': 0, 'downloads': 0}
    def request(method, url, **kwargs):
        if url.endswith('/ack'):
            count['ack'] += 1
            if count['ack'] == 1:
                raise requests.Timeout('Simulated response loss after server commit')
        response = Mock(status_code=200)
        response.json.return_value = {'acknowledged': True} if url.endswith('/ack') else envelope
        if url.endswith('manifest'):
            response.content = manifest
        else:
            response.content = batch_file(envelope, 'csv' if url.endswith('csv') else 'jsonl')[0]
            if url.endswith('csv') or url.endswith('jsonl'): count['downloads'] += 1
        return response
    client.request.side_effect = request
    with TemporaryDirectory() as temp:
        args = dict(base='https://example.com', subscription_id=str(uuid4()), output=temp,
                    site_key='site', customer_key='customer', session=client)
        with pytest.raises(requests.Timeout): pull(**args)
        assert (Path(temp) / envelope['batch_id'] / 'data.jsonl').exists()
        result = pull(**args)
        assert result['acknowledged'] and count == {'ack': 2, 'downloads': 2}
