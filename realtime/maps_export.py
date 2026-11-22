"""Snapshot exports preserve raw fields and guard spreadsheet formulas."""
import csv
import io
import json


def content(job,fmt):
    rows=job['result_cache']['places']
    if fmt=='jsonl':
        # A request envelope keeps provenance and query conditions even for zero results.
        return json.dumps(job,ensure_ascii=False,default=str,separators=(',',':'))+'\n'
    out=io.StringIO(newline='')
    fields=['job_id','provider','provider_version','completed_at','query_conditions_json','data_id','name','formatted_address','primary_type','rating','user_rating_count','national_phone_number','website_uri','latitude','longitude','distance_m','regular_opening_hours_json','source_fields_json','place_id','categories_json','business_status','google_maps_uri','record_json']
    w=csv.DictWriter(out,fieldnames=fields);w.writeheader()
    common=dict(job_id=job['id'],provider=job['result_cache']['provider'],provider_version=job['result_cache']['provider_version'],completed_at=str(job['completed_at']),query_conditions_json=json.dumps(job['parameters'],ensure_ascii=False))
    for p in rows:
        r={k:p.get(k) for k in fields};r.update(common,latitude=p['location']['latitude'],longitude=p['location']['longitude'],regular_opening_hours_json=json.dumps(p['regular_opening_hours'],ensure_ascii=False),source_fields_json=json.dumps(p['source_fields'],ensure_ascii=False))
        r.update(categories_json=json.dumps(p['categories'],ensure_ascii=False),record_json=json.dumps(p,ensure_ascii=False))
        w.writerow({k:("'"+v if isinstance(v,str) and not k.endswith('_json') and v.lstrip().startswith(('=','+','-','@')) else v) for k,v in r.items()})
    return out.getvalue()


def export(handler,job,fmt):
    payload=content(job,fmt).encode('utf-8-sig' if fmt=='csv' else 'utf-8')
    handler.send_response(200)
    handler.send_header('Content-Type','text/csv; charset=utf-8' if fmt=='csv' else 'application/x-ndjson; charset=utf-8')
    handler.send_header('Content-Disposition',f'attachment; filename="maps-{job["id"]}.{fmt}"')
    handler.send_header('Content-Length',str(len(payload)))
    handler.send_header('Cache-Control','no-store')
    handler.end_headers();handler.wfile.write(payload)
