#!/usr/bin/python3
"""Install this reviewed snapshot for the current user. No sudo or downloads."""
import argparse
import json
import os
from pathlib import Path
import shlex
import stat
import tempfile

ROOT=Path(__file__).resolve().parent.parent
NAME='kalshi-bitcoin-bot'


def atomic(path,content,mode=0o644):
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.is_symlink(): raise RuntimeError('Refusing to overwrite a symlink: '+str(path))
    fd,temp=tempfile.mkstemp(prefix='.'+path.name+'-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:
            f.write(content); f.flush(); os.fsync(f.fileno())
        os.chmod(temp,mode)
        os.replace(temp,path)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def install(home=None,desktop=True):
    home=Path(home) if home is not None else Path.home()
    if not home.is_absolute(): raise ValueError('Install home must be absolute')
    base=home/'.local/share'/NAME
    marker=base/'installation.json'
    executable=home/'.local/bin'/NAME
    menu=home/'.local/share/applications'/(NAME+'.desktop')
    shortcut=home/'Desktop'/(NAME+'.desktop')
    if base.is_symlink(): raise RuntimeError('Refusing symlink installation root')
    updating=marker.is_file() and not marker.is_symlink()
    if updating:
        info=json.loads(marker.read_text())
        if info.get('application')!=NAME or info.get('owner')!=os.getuid():
            raise RuntimeError('Unrecognized existing installation')
    elif base.exists() or executable.exists() or menu.exists() or (desktop and shortcut.exists()):
        raise RuntimeError('An unmanaged destination exists; no files were overwritten')
    os.umask(0o077)
    sources=[]
    for pattern in ('kalshi_bot/*.py','docs/*.md','deploy/*.sh','deploy/*.service*','deploy/provision-gateway.py','deploy/setup-system.py','assets/*.svg','README.md'):
        sources.extend(ROOT.glob(pattern))
    for source in sources:
        if not source.is_file() or source.is_symlink(): continue
        relative=source.relative_to(ROOT)
        target=base/relative
        if not target.parent.resolve().is_relative_to(base.resolve()):
            raise RuntimeError('Installation parent escapes managed directory')
        if target.exists() and (target.is_symlink() or not target.is_file()):
            raise RuntimeError('Untrusted installation target')
        mode=0o755 if source.suffix=='.sh' or source.name in ('provision-gateway.py','setup-system.py') else 0o644
        atomic(target,source.read_text(),mode)
    atomic(base/'launch.py','from kalshi_bot.desktop import main\nraise SystemExit(main())\n')
    atomic(executable,'#!/bin/sh\nexec /usr/bin/python3 -B '+shlex.quote(str(base/'launch.py'))+' "$@"\n',0o755)
    def quote_exec(value):
        value=str(value).replace('%','%%')
        for c in ('\\','"','`','$'): value=value.replace(c,'\\'+c)
        return '"'+value+'"'
    entry='\n'.join(['[Desktop Entry]','Type=Application','Version=1.0','Name=Kalshi Bitcoin Bot',
                      'Comment=Offline strategy simulator and optional Kalshi account tools',
                      'Exec='+quote_exec(executable),'TryExec='+str(executable),
                      'Icon='+str(base/'assets/kalshi-bitcoin-bot.svg'),'Terminal=false',
                      'Categories=Office;Finance;','StartupNotify=true','Keywords=Bitcoin;Kalshi;Simulation;Trading;',''])
    atomic(menu,entry)
    if desktop and (home/'Desktop').is_dir(): atomic(shortcut,entry,0o755)
    atomic(marker,json.dumps({'application':NAME,'owner':os.getuid(),'source':str(ROOT),'version':'0.4.0'},indent=2)+'\n',0o600)
    return {'application':str(executable),'menu':str(menu),'desktop':str(shortcut) if desktop and shortcut.exists() else None,'installed_source':str(base)}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--no-desktop-shortcut',action='store_true')
    a=p.parse_args()
    if os.geteuid()==0: p.error('Run as your normal laptop user, not root')
    print(json.dumps(install(desktop=not a.no_desktop_shortcut),indent=2))
    print('Installed locally. No account connected, services enabled, or network rules changed.')


if __name__=='__main__': main()
