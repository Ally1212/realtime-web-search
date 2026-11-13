"""Apply the verified homepage patch to the frozen static release, without restart."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil

BEFORE = '317570007093b0cd0a671cc3260edf1d6858478c49f3d9dfa6fe562878e2c826'
AFTER = '0b47af649d87435cb940124c9a97b9f95db9131219d9e12a6846f71bc3f623bb'
OLD = b"if(first)section?.classList.add('sf-view-loading');"
NEW = b"if(first&&current!=='overview')section?.classList.add('sf-view-loading');"


def deploy(base):
    page = base / 'static/index.html'
    raw = page.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest == AFTER:
        print('Already deployed:', digest)
        return
    if digest != BEFORE or raw.count(OLD) != 1:
        raise RuntimeError('Unexpected production version; refusing to overwrite')
    candidate = raw.replace(OLD, NEW)
    if hashlib.sha256(candidate).hexdigest() != AFTER:
        raise RuntimeError('Candidate hash mismatch')
    backup = base / 'state/home-firstload-20261113/backup/index.html'
    backup.parent.mkdir(parents=True, exist_ok=True)
    if backup.exists():
        if backup.read_bytes() != raw:
            raise RuntimeError('Existing backup differs; refusing to overwrite')
    else:
        shutil.copy2(page, backup)
    temp = page.with_name('index.html.firstload-tmp')
    with temp.open('xb') as stream:
        stream.write(candidate)
    shutil.copymode(page, temp)
    stat = page.stat()
    os.chown(temp, stat.st_uid, stat.st_gid)
    if page.read_bytes() != raw:
        temp.unlink()
        raise RuntimeError('Production changed during deployment')
    os.replace(temp, page)
    if hashlib.sha256(page.read_bytes()).hexdigest() != AFTER:
        raise RuntimeError('Published hash mismatch')
    print('Published:', AFTER)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base', type=Path)
    deploy(parser.parse_args().base)
