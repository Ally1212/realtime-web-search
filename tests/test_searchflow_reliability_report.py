from scripts.searchflow_reliability_report import compute

def test_sql_report_counts_cached_errors_and_recovery_without_fabricating_success():
    def row(id,status,minute,reused=False):
        return dict(request_id=id,source='google_web',query='q',page=1,classification=status,error_code=None,
            completed_at=f'2026-11-09T00:{minute:02}:00+00:00',response={},reused=reused)
    audit=dict(requests=[row('a','http_error',0),row('a','http_error',0,True),row('b','results',2),row('b','results',2,True)],
        new_links_by_source={'google_web':3},campaigns=[dict(status='stopped')])
    r=compute(audit,stop_requested_at='2026-11-09T00:01:00+00:00')
    assert r['fresh_attempts']==2 and r['successful_attempts']==1
    assert r['cached_associations']==2 and r['successful_cached_associations']==1
    assert r['recovered_query_pages']==1 and r['outcomes'][0]['recovery_seconds']==120
    assert r['sources']['google_web']['completed_after_stop_requested']==1
    assert r['new_unique_links']==3 and r['active_owned_tasks']==0
