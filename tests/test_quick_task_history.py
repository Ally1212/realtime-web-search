from http.cookies import SimpleCookie
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import UUID

import pytest

from realtime.access_gate import COOKIE
from realtime.quick_request_http import handle, receipt
from realtime.quick_task_history import BrowserTasks, LIST_PATH, PREFIX

NOW = 1_800_000_000
KEY = 'test-only'
JOB = '12345678-1234-4234-8234-123456789abc'


def session(nonce='1', expires=NOW + 600):
    return f'{COOKIE}={expires}.{nonce * 32}.{"3" * 64}'


def pair(header):
    return header.split(';', 1)[0]


@pytest.fixture(autouse=True)
def clock():
    with patch('realtime.quick_task_history.time.time', return_value=NOW):
        yield


def test_history_cookie_is_per_job_per_session_and_bounded():
    scope = BrowserTasks({'Cookie': session()}, KEY)
    assert not scope.allows(JOB)
    assert not scope.registered()
    granted = scope.remember([JOB])[0]
    assert all(flag in granted for flag in ('HttpOnly', 'Secure', 'SameSite=Strict', 'Path=/api/quick-request', 'Max-Age=600'))
    state = session() + '; ' + pair(granted)
    assert BrowserTasks({'Cookie':state}, KEY).allows(JOB)
    assert not BrowserTasks({'Cookie':session('2') + '; ' + pair(granted)}, KEY).allows(JOB)
    assert not BrowserTasks({'Cookie':session(expires=NOW-1) + '; ' + pair(granted)}, KEY).allows(JOB)
    assert not BrowserTasks({'Cookie':state}, 'rotated').allows(JOB)
    assert not BrowserTasks({'Cookie':state}, KEY).allows(str(UUID(int=2)))
    assert not BrowserTasks({'Cookie':session()+'; '+pair(granted)[:-1]+'z'}, KEY).allows(JOB)
    incoming = '; '.join([session(), *(pair(header) for header in scope.remember(UUID(int=i) for i in range(20)))])
    capped = BrowserTasks({'Cookie':incoming}, KEY)
    trim, add = capped.remember([JOB])
    assert 'Max-Age=0' in trim and 'Max-Age=600' in add


def test_route_migrates_valid_original_receipt_and_allows_read_only():
    cookie = BrowserTasks({'Cookie':session()}, KEY).remember([JOB])[0]
    test_request = SimpleNamespace(
        path=LIST_PATH, headers={'X-SearchFlow-Quick':'1','Cookie':session()},
        config=SimpleNamespace(database_url='unused'), send_json=Mock(), send_response=Mock(),
        send_header=Mock(), end_headers=Mock(), wfile=Mock())
    with patch.dict('os.environ', SEARCHFLOW_PRODUCT_ENABLED='true'), patch(
        'realtime.quick_request_http.secret', return_value=KEY), patch(
        'realtime.product_http.secure', return_value=True), patch(
        'realtime.quick_request_http.ProductStore') as factory:
        store = factory.return_value
        store.authenticate.return_value = {'id':'workspace'}
        handle(test_request, 'GET')
        test_request.send_json.assert_called_once_with({'tasks':[]})
        with patch('realtime.product_http.body',return_value={'receipts':[{'id':JOB,'token':'0'*64}]}):
            handle(test_request, 'POST')
            test_request.send_json.assert_called_with({'remembered':[]},200)
            test_request.send_header.assert_not_called()
        with patch('realtime.product_http.body',return_value={'receipts':[{'id':JOB,'token':receipt(KEY,JOB)}]}):
            handle(test_request, 'POST')
            test_request.send_header.assert_any_call('Set-Cookie',cookie)
        test_request.headers['Cookie'] = session()+'; '+pair(cookie)
        test_request.path = '/api/quick-request/'+JOB
        handle(test_request, 'GET')
        store.job.assert_called_once_with('workspace',JOB)
        test_request.path += '/cancel'
        handle(test_request, 'POST')
        assert test_request.send_json.call_args.args[1] == 404
        store.cancel.assert_not_called()


def test_new_accepted_task_binds_browser_history():
    handler = SimpleNamespace(path='/api/quick-request',
        headers={'X-SearchFlow-Quick':'1','Cookie':session()}, config=SimpleNamespace(database_url='unused'),
        send_json=Mock(), send_response=Mock(), send_header=Mock(), end_headers=Mock(), wfile=Mock())
    with patch.dict('os.environ', SEARCHFLOW_PRODUCT_ENABLED='true'), patch(
        'realtime.quick_request_http.secret', return_value=KEY), patch(
        'realtime.product_http.secure', return_value=True), patch(
        'realtime.product_http.body',return_value={'query':'test','source':'google_web'}), patch(
        'realtime.quick_request_http.ProductStore') as factory:
        store = factory.return_value
        store.authenticate.return_value = {'id':'workspace'}
        store.submit.return_value = (JOB,False)
        handle(handler,'POST')
        handler.send_response.assert_called_once_with(202)
        header = next(call.args[1] for call in handler.send_header.call_args_list if call.args[0]=='Set-Cookie')
        parsed = SimpleCookie(header)
        assert PREFIX + UUID(JOB).hex in parsed
        assert BrowserTasks({'Cookie':session()+'; '+pair(header)},KEY).allows(JOB)
