"""Pinned seven-file transaction. Default is read-only check; no activation.

No SSH, restart, migration, dependency installation, source or platform calls.
A fresh paused-state snapshot is supplied by the trusted operator. This is a
file transaction, not an atomic deployment barrier for running processes.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import tarfile

if __package__:
    from . import google_data_release_guard as common
else:
    import google_data_release_guard as common

BUNDLE_SHA256='96576de24f089a96ce03d214875bf3fc13b67e8dff835daa379e2a69ae1820c1'
MANIFEST_SHA256='e434825d034d09a287b360563118e59fa06717b8bd85bde697dc8a59b628cfb3'
MANIFEST_CANONICAL_SHA256='8900a386415ccc620c2d9b201c5830992c4656662c7cb832e3c61534ba6bcdab'
COMMON_SHA256='bba09e4ab0167c64df1d20c6d727e28917da784e5686e07641e9ebae05d9ebec'
NAMES=frozenset({'google_feeds.py','google_raw_capture.py','google_feed_capture_journal.py',
    'google_feed_capture_rights.py','google_registry.py','google_domain.py','google_rights.py'})
NATIVE_ROOT=Path('/home/azureuser/projects/realtime-web-search-openserp/state/field-expansion-20261016/code-r2/local-worker/realtime')
STATE_ROOT=Path('/home/azureuser/projects/realtime-web-search-openserp/state')
require=common.require
read_regular=common.read_regular
atomic_write=common.atomic_write
sha=common.digest


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()


def validate_manifest(manifest):
    require(sha(canonical(manifest))==MANIFEST_CANONICAL_SHA256,'unreviewed manifest')
    require(set(manifest['files'])==NAMES,'unexpected candidate modules')
    require(all(Path(n).name==n and n.endswith('.py') for n in manifest['protected_files']),'invalid protected path')


def load_manifest(manifest_path):
    require(sha(read_regular(Path(common.__file__)))==COMMON_SHA256,'release helper changed')
    raw=read_regular(Path(manifest_path));require(sha(raw)==MANIFEST_SHA256,'manifest digest differs')
    manifest=json.loads(raw);validate_manifest(manifest)
    return manifest


def load_review(bundle,manifest_path):
    manifest=load_manifest(manifest_path)
    bundle=Path(bundle);require(sha(read_regular(bundle))==BUNDLE_SHA256,'bundle digest differs')
    with tarfile.open(bundle) as tar:
        members=tar.getmembers()
        require(len(members)==7 and {m.name for m in members}=={'realtime/'+n for n in NAMES},'archive member set differs')
        require(all(m.isfile() and 0<m.size<=1024*1024 for m in members),'invalid archive file')
        content={Path(m.name).name:tar.extractfile(m).read() for m in members}
    validate_content(manifest,content)
    return manifest,content


def validate_content(manifest,content):
    require(set(content)==NAMES,'candidate content missing')
    for name,raw in content.items():
        require(sha(raw)==manifest['files'][name]['after'],'candidate digest differs: '+name)
        compile(raw,name,'exec')  # never exec candidate code during installation


def fsync_directory(path):
    fd=os.open(path,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


def protected(manifest,root):
    for name,expected in manifest['protected_files'].items():
        require(sha(read_regular(root/name))==expected,'protected file changed: '+name)
    allowed=set(manifest['protected_files'])|NAMES
    require({p.name for p in root.glob('*.py')}<=allowed,'unreviewed module in package')


def absent(path):return not path.exists() and not path.is_symlink()


def before_files(manifest,root):
    protected(manifest,root)
    for name,h in manifest['files'].items():
        if h['before'] is None:require(absent(root/name),'new path already exists: '+name)
        else:require(sha(read_regular(root/name))==h['before'],'baseline changed: '+name)


def replace_owner(path):
    info=path.stat();parent=path.parent.stat()
    gid=parent.st_gid if parent.st_mode & stat.S_ISGID else os.getegid()
    require(info.st_uid==os.geteuid() and info.st_gid==gid,'replacement would change source ownership')
    return [info.st_uid,info.st_gid]


def save_state(journal,state):atomic_write(journal/'journal.json',json.dumps(state,sort_keys=True).encode(),0o600)


def verify_state(manifest,root,journal,state):
    require(state['root']==str(root) and state['bundle_sha256']==BUNDLE_SHA256
            and state['manifest_sha256']==MANIFEST_CANONICAL_SHA256,'journal binding differs')
    old={n for n,h in manifest['files'].items() if h['before'] is not None}
    require(set(state['old_modes'])==old and all(type(v) is int and 0<=v<=0o777 for v in state['old_modes'].values()),'invalid original modes')
    require(set(state['old_owners'])==old and state['operator_uid']==os.geteuid(),'operator/owner binding differs')
    require(set(state['anchors'])<=(NAMES-old),'invalid new-file ownership set')


def rollback_preflight(manifest,root,journal,state):
    protected(manifest,root)
    # Check every backup, current target and ownership anchor before restoring
    # or deleting any file. Same bytes alone do not prove ownership of a NEW file.
    for name,h in manifest['files'].items():
        path=root/name
        if h['before'] is not None:
            require(sha(read_regular(journal/'old'/name))==h['before'],'backup changed: '+name)
            require(sha(read_regular(path)) in {h['before'],h['after']},'target edited: '+name)
            require(stat.S_IMODE(path.stat().st_mode)==state['old_modes'][name],'target mode edited: '+name)
            require(replace_owner(path)==state['old_owners'][name],'source ownership changed')
        else:
            anchor=journal/'new'/name
            if name in state['anchors']:
                require(sha(read_regular(anchor))==h['after'],'anchor changed: '+name)
                info=anchor.stat();require([info.st_dev,info.st_ino,info.st_uid,info.st_gid]==state['anchors'][name],'anchor identity changed: '+name)
            if not absent(path):
                require(name in state['anchors'],'new-file ownership unavailable: '+name)
                require(sha(read_regular(path))==h['after'] and path.samefile(anchor),'new file replaced or edited: '+name)
                require(stat.S_IMODE(path.stat().st_mode)==0o644,'new file mode edited: '+name)


def operate(action,manifest,content,root,journal,snapshot):
    validate_manifest(manifest)
    require(action in {'check','install','rollback'},'unknown transaction action')
    snapshot_hash=common.check_snapshot(snapshot)
    root=Path(root).absolute();journal=Path(journal).absolute()
    require(root.resolve()==root and root.is_dir(),'invalid native root')
    require(journal.resolve()==journal and journal.parent.is_dir(),'invalid backup path')
    require(not journal.is_relative_to(root) and not root.is_relative_to(journal),'backup overlaps package')
    if action=='check':
        validate_content(manifest,content);before_files(manifest,root)
        require(root.stat().st_dev==journal.parent.stat().st_dev,'backup must share package filesystem')
        require(all(not stat.S_IMODE((root/n).stat().st_mode) & 0o7000
                    for n,h in manifest['files'].items() if h['before'] is not None),'unsupported special module permissions')
        for name,h in manifest['files'].items():
            if h['before'] is not None:replace_owner(root/name)
        return dict(status='checked',files=7,creates_files=False,activates_capture=False)
    # Cooperating transactions share this lock even if backup names differ.
    lock=os.open(root.parent/'.sf-feed-capture-release.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
    try:
        require(stat.S_ISREG(os.fstat(lock).st_mode) and not os.fstat(lock).st_mode & 0o077,'invalid transaction lock')
        fcntl.flock(lock,fcntl.LOCK_EX)
        common.check_snapshot(snapshot)
        if action=='install':
            validate_content(manifest,content);before_files(manifest,root)
            require(absent(journal),'backup already exists; no install replay')
            # Hardlink anchors make new-file ownership survive a crash between
            # publish and acknowledgement. They must be on this same filesystem.
            require(root.stat().st_dev==journal.parent.stat().st_dev,'backup must share package filesystem')
            require(all(not stat.S_IMODE((root/n).stat().st_mode) & 0o7000
                        for n,h in manifest['files'].items() if h['before'] is not None),'unsupported special module permissions')
            old_owners={n:replace_owner(root/n) for n,h in manifest['files'].items() if h['before'] is not None}
            journal.mkdir(mode=0o700);(journal/'old').mkdir(mode=0o700);(journal/'new').mkdir(mode=0o700)
            state=dict(root=str(root),bundle_sha256=BUNDLE_SHA256,manifest_sha256=MANIFEST_CANONICAL_SHA256,
                snapshot_sha256=snapshot_hash,old_owners=old_owners,operator_uid=os.geteuid(),phase='preparing',intent=None,anchors={},backups_complete=False,
                old_modes={n:stat.S_IMODE((root/n).stat().st_mode) for n,h in manifest['files'].items() if h['before'] is not None})
            save_state(journal,state)
            for name,h in manifest['files'].items():
                if h['before'] is not None:
                    raw=read_regular(root/name);require(sha(raw)==h['before'],'baseline changed during backup')
                    atomic_write(journal/'old'/name,raw,0o600)
                    require(sha(read_regular(journal/'old'/name))==h['before'],'backup verification failed')
            before_files(manifest,root)
            state.update(phase='ready',backups_complete=True);save_state(journal,state)
            for name in sorted(NAMES):
                common.check_snapshot(snapshot);protected(manifest,root)
                h=manifest['files'][name];path=root/name
                state.update(phase='installing',intent=name);save_state(journal,state)
                if h['before'] is None:
                    require(absent(path),'new path concurrently created: '+name)
                    anchor=journal/'new'/name
                    fd=os.open(anchor,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o644)
                    with os.fdopen(fd,'wb') as stream:
                        os.fchmod(stream.fileno(),0o644);stream.write(content[name]);stream.flush();os.fsync(stream.fileno())
                    fsync_directory(anchor.parent)
                    require(sha(read_regular(anchor))==h['after'],'anchor verification failed')
                    info=anchor.stat();state['anchors'][name]=[info.st_dev,info.st_ino,info.st_uid,info.st_gid];save_state(journal,state)
                    os.link(anchor,path)  # exclusive; a concurrent creator is never overwritten
                    fsync_directory(root)
                else:
                    require(sha(read_regular(path))==h['before'],'target changed before replacement: '+name)
                    require(stat.S_IMODE(path.stat().st_mode)==state['old_modes'][name],'target mode changed before replacement')
                    require(replace_owner(path)==state['old_owners'][name],'source ownership changed before replacement')
                    require(sha(read_regular(journal/'old'/name))==h['before'],'backup changed before replacement')
                    atomic_write(path,content[name],state['old_modes'][name])
                require(sha(read_regular(path))==h['after'],'installed digest differs')
            protected(manifest,root)
            state.update(phase='installed',intent=None);save_state(journal,state)
        else:
            require(journal.is_dir() and not journal.stat().st_mode & 0o077,'private backup required')
            state=json.loads(read_regular(journal/'journal.json'));verify_state(manifest,root,journal,state)
            require(state['phase'] in {'preparing','ready','installing','installed','rolling_back','rolled_back'},'invalid journal phase')
            require(type(state['backups_complete']) is bool,'invalid backup completion state')
            if not state['backups_complete']:
                require(state['phase'] in {'preparing','rolled_back'},'incomplete backups after mutation')
                # A backup interruption precedes every source mutation.
                before_files(manifest,root)
            else:
                rollback_preflight(manifest,root,journal,state)
                state.update(phase='rolling_back',intent=None);save_state(journal,state)
                for name in sorted(NAMES):
                    common.check_snapshot(snapshot);protected(manifest,root)
                    h=manifest['files'][name];path=root/name
                    if h['before'] is None:
                        if not absent(path):
                            require(path.samefile(journal/'new'/name) and sha(read_regular(path))==h['after']
                                    and stat.S_IMODE(path.stat().st_mode)==0o644,'new file changed before removal')
                            try:path.unlink()
                            finally:fsync_directory(root)
                    elif sha(read_regular(path))!=h['before']:
                        require(sha(read_regular(path))==h['after'],'target changed during rollback')
                        require(stat.S_IMODE(path.stat().st_mode)==state['old_modes'][name],'target mode changed during rollback')
                        require(replace_owner(path)==state['old_owners'][name],'source ownership changed during rollback')
                        raw=read_regular(journal/'old'/name);require(sha(raw)==h['before'],'backup changed during rollback')
                        atomic_write(path,raw,state['old_modes'][name])
                before_files(manifest,root)
            state.update(phase='rolled_back',intent=None);save_state(journal,state)
        return dict(status=state['phase'],files=7,activates_capture=False,restarts=0,migrations=0)
    finally:os.close(lock)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('check','install','rollback'),nargs='?',default='check')
    parser.add_argument('--bundle',type=Path);parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--snapshot',type=Path,required=True);parser.add_argument('--journal',type=Path,required=True)
    args=parser.parse_args(argv)
    journal=args.journal.absolute()
    require(journal.is_relative_to(STATE_ROOT),'backup must be inside project state')
    if args.action=='rollback':manifest,content=load_manifest(args.manifest),{}
    else:
        require(args.bundle is not None,'candidate bundle required for check/install')
        manifest,content=load_review(args.bundle,args.manifest)
    result=operate(args.action,manifest,content,NATIVE_ROOT,journal,json.loads(read_regular(args.snapshot)))
    print(json.dumps(result))


if __name__=='__main__':main()
