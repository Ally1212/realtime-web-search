"""Read-only diagnosis of interrupted one-shot journals; never authorizes replay.

Does not create a directory/lock, update counters, change phases, or invoke
acquisition/validation/bulk. Absence of evidence is never evidence of failure.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from uuid import UUID

from .google_data import digest
from .google_serp_delivery import saved_web_run_message,with_search_snapshot_text
from .google_web_once import web_plan
from .google_whale_readback import ReadbackTarget


def _read(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size>65536:
        raise ValueError('invalid private journal file')
    return json.loads(path.read_text())


def inspect_journal(directory, *, expected_manifest_hash, reader):
    root=Path(directory).absolute()
    if root.resolve()!=root or not root.is_dir() or root.stat().st_mode & 0o077:
        raise ValueError('existing private journal required')
    execution=_read(root/'execution.json')
    if digest(execution)!=expected_manifest_hash or execution.get('journal')!=str(root):
        raise ValueError('execution binding differs')
    plan=execution['plan']
    if plan!=web_plan(**{k:plan[k] for k in ('dataset_id','source_name','collector_id')}):
        raise ValueError('unexpected execution scope')
    state=_read(root/'state.json')
    if state.get('plan_hash')!=digest(plan):
        raise ValueError('journal plan differs')
    report=dict(phase=state['phase'],status='outcome_unknown',journal_changed=False,
        replay_authorized=False,formal_admission=False,request_id=None,
        source_reads=0,receipt_reads=0,pinned_reads=0)
    request_id=state.get('request_id')
    if request_id is None and state['phase']=='source_intent' and (root/'source-proof.json').exists():
        proof=_read(root/'source-proof.json')
        ids=proof.get('saved_request_ids')
        if isinstance(ids,list) and len(ids)==1:
            request_id=ids[0]
    if request_id is None:
        return report
    request_id=str(UUID(str(request_id)));report['request_id']=request_id
    report['source_reads']=1
    row=reader.saved_public_request(request_id)
    if (str(row['request_id'])!=request_id or row['source']!='google_web'
            or row['query']!=plan['query'] or row['page']!=1):
        raise ValueError('saved source identity differs')
    completed=row['completed_at']
    if isinstance(completed,str):completed=datetime.fromisoformat(completed.replace('Z','+00:00'))
    if completed<datetime.fromisoformat(state['source_not_before']):
        raise ValueError('historical source cannot replace interrupted request')
    if row['classification']!='results':
        report.update(status='saved_source_nonresults',classification=row['classification'])
        return report
    message=with_search_snapshot_text(saved_web_run_message(row,
        **{k:plan[k] for k in ('dataset_id','source_name','collector_id')}))
    conditions=message['content']['platform_payload']['observation']['query_conditions']
    if str(conditions.get('gl','')).upper()!=plan['country'] or conditions.get('hl')!=plan['language']:
        raise ValueError('saved source conditions differ')
    if state.get('message_hash') and digest(message)!=state['message_hash']:
        raise ValueError('saved source changed')
    report.update(status='saved_source_verified',occurrence_count=len(
        message['content']['platform_payload']['observation']['occurrences']))
    if state['phase'] not in {'bulk_intent','awaiting_terminal','terminal','verified'}:
        return report
    if not state.get('message_hash'):
        raise ValueError('submitted event binding unavailable')
    # Honor the existing cooldown without writing or consuming another poll.
    if state.get('receipt_not_before'):
        from datetime import timezone
        if datetime.now(timezone.utc)<datetime.fromisoformat(state['receipt_not_before']):
            report.update(status='receipt_cooldown',receipt_not_before=state['receipt_not_before'])
            return report
    ack=state.get('acknowledgment',{})
    receipt=reader.receipt(message,ack);report['receipt_reads']=1
    query=dict(dataset_id=message['dataset_id'],**{k:message['source'][k]
        for k in ('source_platform','source_name','source_record_key')})
    if any(receipt.get(k)!=v for k,v in query.items()):
        raise ValueError('receipt event differs')
    for key in ('document_id','version_id'):
        if ack.get(key) and receipt.get(key)!=ack[key]:raise ValueError('receipt version differs')
        if state.get('target',{}).get(key) and receipt.get(key)!=state['target'][key]:
            raise ValueError('previously pinned target differs')
    status=receipt.get('status')
    if status not in {'accepted','duplicate'}:
        report.update(status='writer_rejected' if status in {'failed','rejected'} else 'writer_not_terminal',
            writer_status=status)
        return report
    target=ReadbackTarget(receipt['document_id'],receipt['version_id'],plan['dataset_id'],
        'google_search',expected_platform_payload_hash=message['source']['payload_hash'])
    expected=message['content']['platform_payload']
    result=reader.readback(target,expected);report['pinned_reads']=1
    if (digest(result['payload'])!=digest(expected)
            or result['proof'].get('payload_binding')!='pinned_version'
            or result['proof'].get('payload_roundtrip_verified') is not True):
        raise ValueError('independent fixed payload differs')
    report.update(status='writer_terminal_and_pinned_verified',writer_status=status,
        document_id=target.document_id,version_id=target.version_id)
    return report
