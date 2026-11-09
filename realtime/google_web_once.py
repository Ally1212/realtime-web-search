"""Bounded Web delivery controller; adapters and explicit operator consent required.

No daemon, queue toggles, source discovery, HTTP client, or production defaults.
Intent is fsynced before each non-repeatable call. An uncertain call never gets
replayed by this controller, including after process death.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import fcntl
import json
import os
from pathlib import Path
import tempfile
from uuid import UUID

from .google_data import digest
from .google_serp_delivery import saved_web_run_message, with_search_snapshot_text
from .google_whale_readback import ReadbackTarget


def web_plan(*, dataset_id, source_name, collector_id):
    for value in (dataset_id, source_name, collector_id):
        if not isinstance(value, str) or not value.strip() or len(value) > 256:
            raise ValueError('explicit delivery identity required')
    return dict(version='web-once-plan-1', query='solar energy research 2026',
        country='US', language='en', page=1, source='google_web', provider='openserp',
        max_source_calls=1, max_bulk_calls=1, max_receipt_polls=10,
        dataset_id=dataset_id, source_name=source_name, collector_id=collector_id,
        automatic_flags=False, body_collection=False, formal_admission=False)


def _write(path, value):
    fd, name = tempfile.mkstemp(prefix='.once-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class WebOnce:
    """Adapter contract:

    assert_idle(plan): independently checks off flags/no unrelated live work.
    acquire_once(plan): at most one OpenSERP backend request; saves outcome first;
      returns request UUID, even on failure. Must not create an active campaign.
    saved_public_request(uuid): independently reloads public saved source row.
    validate(message): returns True only on successful platform validation.
    bulk_once(message): sends ONE message, returns receipt dict (no retry).
    receipt(message, ack): exact stable event read, returns processing receipt.
    readback(target, expected_payload): uses independent Consumer pinned version,
      returns read_full_run result with raw payload and proof.

    These are required capabilities, not supplied live adapters. This controller
    alone is not an executable production acceptance test.
    """
    def __init__(self, directory, plan, adapter):
        expected = web_plan(**{k: plan[k] for k in ('dataset_id','source_name','collector_id')})
        if plan != expected:
            raise ValueError('unsupported one-shot scope')
        self.root = Path(directory).absolute()
        if self.root.is_symlink() or self.root.resolve() != self.root:
            raise ValueError('journal path must not contain symlinks')
        self.root.mkdir(mode=0o700, exist_ok=True)
        if self.root.stat().st_mode & 0o077:
            raise ValueError('journal must be private')
        self.plan, self.adapter = deepcopy(plan), adapter
        self.plan_hash = digest(plan)
        self.path = self.root/'state.json'

    @contextmanager
    def locked(self):
        fd = os.open(self.root/'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)

    def _save(self, state, phase, **updates):
        state.update(updates, phase=phase, updated_at=datetime.now(timezone.utc).isoformat())
        _write(self.path, state)

    def _ready(self):
        if (self.root/'STOP').exists() or (self.root/'STOP').is_symlink():
            raise ValueError('operator stop requested')
        self.adapter.assert_idle(deepcopy(self.plan))

    def _row(self, state):
        row = self.adapter.saved_public_request(state['request_id'])
        if (str(UUID(str(row['request_id']))) != state['request_id']
                or row['source'] != 'google_web' or row['query'] != self.plan['query']
                or row['page'] != 1 or row['classification'] != 'results'):
            raise ValueError('successful saved public Web request required')
        message = with_search_snapshot_text(saved_web_run_message(row,
            **{k:self.plan[k] for k in ('dataset_id','source_name','collector_id')}))
        conditions = message['content']['platform_payload']['observation']['query_conditions']
        # Actual request conditions must match; device/cache/precise location stay unknown.
        if (str(conditions.get('gl','')).upper() != self.plan['country']
                or conditions.get('hl') != self.plan['language']):
            raise ValueError('saved request conditions differ')
        completed = datetime.fromisoformat(message['content']['platform_payload']['observation']['completed_at'].replace('Z','+00:00'))
        if completed < datetime.fromisoformat(state['source_not_before']):
            raise ValueError('historical request cannot substitute for new acquisition')
        if state.get('message_hash') and digest(message) != state['message_hash']:
            raise ValueError('saved source changed after preparation')
        return message

    def advance(self, *, authorized_plan_hash):
        if authorized_plan_hash != self.plan_hash:
            raise ValueError('explicit authorization for exact plan required')
        with self.locked():
            if self.path.is_symlink():
                raise ValueError('invalid journal file')
            state = json.loads(self.path.read_text()) if self.path.exists() else dict(
                plan_hash=self.plan_hash, phase='prepared', receipt_polls=0)
            if state['plan_hash'] != self.plan_hash:
                raise ValueError('journal belongs to another plan')
            phase = state['phase']
            # Never infer failure from timeout or crash. No automatic mutation replay.
            if phase in {'source_intent','validation_intent','bulk_intent','rejected','failed','verified'}:
                return state
            self._ready()
            if phase == 'prepared':
                self._save(state, 'source_intent', source_not_before=datetime.now(timezone.utc).isoformat())
                try:
                    request_id = str(UUID(str(self.adapter.acquire_once(deepcopy(self.plan)))))
                except Exception:
                    # No exception text: it may contain a URL, key, or DSN.
                    return state
                self._save(state, 'saved', request_id=request_id)
            if state['phase'] == 'saved':
                self._ready()
                message = self._row(state)
                self._save(state, 'validation_intent', message_hash=digest(message))
                if self.adapter.validate(message) is not True:
                    self._save(state, 'rejected', reason='validation_not_passed')
                    return state
                self._save(state, 'validated')
            if state['phase'] == 'validated':
                self._ready()
                message = self._row(state)
                self._save(state, 'bulk_intent')
                try:
                    ack = self.adapter.bulk_once(message)
                except Exception:
                    return state
                # Keep only fixed IDs; never persist arbitrary platform/exception text.
                ack = {k:ack[k] for k in ('document_id','version_id') if k in ack}
                self._save(state, 'awaiting_terminal', acknowledgment=ack)
            if state['phase'] == 'awaiting_terminal':
                if state['receipt_polls'] >= self.plan['max_receipt_polls']:
                    return state
                if (state.get('receipt_not_before') and datetime.now(timezone.utc) <
                        datetime.fromisoformat(state['receipt_not_before'])):
                    return state
                message = self._row(state)
                self._save(state, 'awaiting_terminal', receipt_polls=state['receipt_polls']+1)
                try:
                    receipt = self.adapter.receipt(message, state['acknowledgment'])
                except Exception as error:
                    if getattr(error,'status_code',None) not in {429,503}:
                        raise
                    from .whale_serp import retry_delay
                    delay = retry_delay(state['receipt_polls'],getattr(error,'retry_after',None))
                    self._save(state,'awaiting_terminal',receipt_not_before=(
                        datetime.now(timezone.utc)+timedelta(seconds=delay)).isoformat())
                    return state
                query = dict(dataset_id=message['dataset_id'], **{k:message['source'][k]
                    for k in ('source_platform','source_name','source_record_key')})
                if any(receipt.get(k) != v for k,v in query.items()):
                    raise ValueError('receipt event scope differs')
                status = receipt.get('status')
                if status in {'failed','rejected'}:
                    self._save(state, 'rejected', reason='writer_rejected')
                    return state
                if status not in {'accepted','duplicate'}:
                    return state
                # IDs must bind to the acknowledgement if already known.
                for key, value in state['acknowledgment'].items():
                    if receipt.get(key) != value:
                        raise ValueError('receipt identity differs')
                target = ReadbackTarget(receipt['document_id'],receipt['version_id'],
                    self.plan['dataset_id'],'google_search',
                    expected_platform_payload_hash=message['source']['payload_hash'])
                self._save(state, 'terminal', target=target.__dict__, writer_status=status)
            if state['phase'] == 'terminal':
                message = self._row(state)
                expected = message['content']['platform_payload']
                result = self.adapter.readback(ReadbackTarget(**state['target']), expected)
                proof = result['proof']
                if (digest(result['payload']) != digest(expected)
                        or proof.get('payload_binding') != 'pinned_version'
                        or proof.get('payload_roundtrip_verified') is not True):
                    raise ValueError('independent pinned payload differs')
                self._save(state, 'verified', occurrence_count=len(expected['observation']['occurrences']),
                    formal_admission=False)
            return state
