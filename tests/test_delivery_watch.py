from datetime import datetime, timezone, timedelta
from scripts.watch_data_delivery import step


def sample(**overrides):
    return dict(batch_sha256='fixed', exports={'csv': {'sha256': 'same', 'bytes': 2}}, source_requests=10,
                storage_bytes=100, **overrides)


def test_watch_keeps_deadline_across_restarts_and_does_not_claim_early_completion():
    now = datetime(2026, 11, 10, tzinfo=timezone.utc)
    s = step(None, sample(), now=now, days=7, interval=86400)
    assert s['status'] == 'running'
    deadline = s['deadline']
    for day in range(1, 7):
        s = step(s, sample(), now=now + timedelta(days=day), days=30, interval=86400)
        assert s['status'] == 'running' and s['deadline'] == deadline
    s = step(s, None, now=now + timedelta(days=7), days=7, interval=86400)
    assert s['status'] == 'completed' and s['successes'] == 7


def test_watch_records_gaps_and_hash_failures_instead_of_blessing_elapsed_time():
    now = datetime(2026, 11, 10, tzinfo=timezone.utc)
    s = step(None, sample(), now=now, days=7, interval=900)
    changed = sample(); changed['exports'] = {}
    s = step(s, changed, now=now + timedelta(hours=1), days=7, interval=900)
    assert s['gaps'] == 1 and s['failures'] == 1
    s = step(s, None, now=now + timedelta(days=7), days=7, interval=900)
    assert s['status'] == 'completed_with_issues'


def test_watch_probe_is_read_only_and_preserves_raw_exception_secrets():
    now = datetime(2026, 11, 10, tzinfo=timezone.utc)
    s = step(None, None, now=now, days=7, interval=900, error='OperationalError')
    assert s['failures'] == 1 and s['last_error'] == 'OperationalError'
