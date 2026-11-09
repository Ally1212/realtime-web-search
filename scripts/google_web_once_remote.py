"""Run the staged one-shot plan/preflight; advance requires explicit consent.

Consumer credential is read only for advance and sent through SSH stdin, then
docker stdin into process memory. It never enters argv, files or printed output.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

PLAN_HASH='04f34264d025ce5bc6c7694c37781d5c7390a12d18652d4addb2ab767a36a01d'
EXECUTION_REVIEW_SUSPENDED=True  # v2 awaits explicit execution consent and Consumer read recovery.
ROOT=Path(__file__).resolve().parents[1]
REVIEW=ROOT/'state/google-data-closure-20261106/web-once-v2/staging/actual-plan-preflight.json'


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('plan','preflight','advance'),nargs='?',default='plan')
    parser.add_argument('--authorized-manifest-hash')
    args=parser.parse_args(argv)
    if args.mode=='advance' and args.authorized_manifest_hash!=PLAN_HASH:
        parser.error('explicit authorization for the reviewed manifest required')
    if args.mode=='advance' and EXECUTION_REVIEW_SUSPENDED:
        parser.error('v2 execution awaits explicit consent and Consumer read recovery')
    reviewed=json.loads(REVIEW.read_text())
    if reviewed['results']['plan']['response']['manifest_hash']!=PLAN_HASH:
        parser.error('local review binding changed')
    packet=dict(mode=args.mode,cli_args=reviewed['cli_args'],authorization=args.authorized_manifest_hash)
    if args.mode=='advance':
        key_path=Path.home()/'.config/searchflow/whale-consumer-read-key'
        if key_path.is_symlink() or key_path.stat().st_mode & 0o077:
            parser.error('private Consumer credential file required')
        packet['consumer_key']=key_path.read_text().strip()
        if not packet['consumer_key']:parser.error('Consumer credential missing')
    # The outer script is supplied over SSH stdin. The nested script is supplied
    # over docker stdin, keeping credential material out of process arguments.
    inner="""import sys,os,json,io
from contextlib import redirect_stdout
sys.path.insert(0,'/app/state/web-once-20261106-v2')
packet=PACKET
if packet.get('consumer_key'):
 os.environ['SEARCHFLOW_ONCE_CONSUMER_KEY']=packet.pop('consumer_key')
from realtime.google_web_once_cli import main
if packet['mode']=='advance':
 from realtime.google_whale_readback import WhaleReadbackClient,ReadbackTarget
 from realtime.config import Config
 config=Config()
 origin=packet['cli_args'][packet['cli_args'].index('--platform-origin')+1]
 try:
  reader=WhaleReadbackClient(origin,os.environ['SEARCHFLOW_ONCE_CONSUMER_KEY'],min_interval_seconds=1)
  reader.verify(ReadbackTarget('doc_9fb5017c5add01bc17809c40dade7d60',
   'ver_4a29e4654356820e56abd6407398d5b0',config.whale_dataset_id,'google_search'))
 except Exception:
  print(json.dumps(dict(exit_code=2,response=dict(phase='blocked_consumer_read_preflight',executed=False))))
  raise SystemExit(0)
args=[packet['mode'],*packet['cli_args']]
if packet.get('authorization'):args+=['--authorized-manifest-hash',packet['authorization']]
output=io.StringIO()
with redirect_stdout(output):status=main(args)
print(json.dumps(dict(exit_code=status,response=json.loads(output.getvalue()))))
""".replace('PACKET',repr(packet))
    outer='import subprocess\nr=subprocess.run(["docker","exec","-e","PYTHONDONTWRITEBYTECODE=1","-i","realtime-web-search-openserp-local-worker-1","python","-"],input='+repr(inner)+',text=True,capture_output=True)\nif r.returncode: raise RuntimeError("remote CLI invocation failed")\nprint(r.stdout)\n'
    try:
        result=subprocess.run(['ssh','-i',str(Path.home()/'.ssh/id_ed25519'),'-o','IdentitiesOnly=yes',
            '-o','ConnectTimeout=15','azureuser@20.106.103.139','python3 -'],
            input=outer,text=True,capture_output=True,timeout=180)
        if result.returncode:raise RuntimeError()
        reply=json.loads(result.stdout)
        if args.mode=='plan' and reply['response'].get('manifest_hash')!=PLAN_HASH:
            raise RuntimeError()
        print(json.dumps(reply))
        return reply['exit_code']
    except Exception:
        print(json.dumps(dict(status='remote_outcome_unknown_no_automatic_retry')))
        return 2


if __name__=='__main__':raise SystemExit(main())
