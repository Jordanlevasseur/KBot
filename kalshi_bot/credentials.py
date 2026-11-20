"""Local-only credential configuration; never makes an account connection."""
import argparse
import getpass
import json
import os
import stat
from pathlib import Path
from .api import Signer


def private_read(path,max_size=65536):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
            raise ValueError('Credential must be an owner-only regular file owned by this user')
        with os.fdopen(fd,'rb',closefd=False) as stream:
            content=stream.read(max_size+1)
        if len(content)>max_size:
            raise ValueError('Credential file too large')
        return content
    finally:
        os.close(fd)


def load(directory,environment,signer_class=Signer):
    directory=Path(directory)
    info=directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
        raise ValueError('Credential directory must be owner-only and not a symlink')
    config=json.loads(private_read(directory/'config.json'))
    if not isinstance(config,dict) or config.get('environment')!=environment:
        raise ValueError('Credential environment mismatch')
    return signer_class(config['key_id'],private_read(directory/'private.pem'))


def configure(directory,environment,key_id,key_file):
    if environment not in ("demo","production"):
        raise ValueError("Invalid credential environment")
    directory=Path(directory).expanduser().resolve(strict=False)
    repo=Path(__file__).resolve().parent.parent
    if directory==repo or repo in directory.parents:
        raise ValueError('Keep credentials outside the source repository')
    pem=private_read(key_file)
    Signer(key_id,pem)  # Validate before writing anything.
    if directory.exists() or directory.is_symlink():
        raise ValueError('Choose a new credential directory; existing credentials are never overwritten')
    directory.mkdir(mode=0o700,parents=False)
    try:
        for name,content in [('private.pem',pem),('config.json',json.dumps({'environment':environment,'key_id':key_id}).encode())]:
            fd=os.open(directory/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'wb') as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
    except BaseException:
        # Preserve partial configuration for diagnosis; never silently reuse it.
        raise


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory',required=True,help='New private directory outside the checkout; parent must exist')
    p.add_argument('--environment',choices=['demo','production'],required=True)
    p.add_argument('--check',action='store_true',help='Validate locally without connection')
    a=p.parse_args()
    try:
        if a.check:
            load(a.directory,a.environment)
        else:
            key_id=getpass.getpass('API key ID (hidden): ').strip()
            key_file=input('Path to downloaded private PEM file (file must be mode 600): ').strip()
            configure(a.directory,a.environment,key_id,key_file)
        print('Credentials validated locally. No connection attempted. No key material printed.')
    except (OSError,ValueError,KeyError):
        p.exit(1,'Credential setup failed. Check file ownership, mode 600, key format, environment, and directory permissions.\n')


if __name__=='__main__':
    main()
