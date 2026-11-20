"""Local Cinnamon cookie acquire/release; never lock/unlock or contact an account."""
from pathlib import Path
import json
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import dbus
from kalshi_bot.power import ScreenLockGuard
inspector=dbus.SessionBus(private=True)
manager=dbus.Interface(inspector.get_object(ScreenLockGuard.SERVICE,ScreenLockGuard.PATH),ScreenLockGuard.SERVICE)
before=set(str(p) for p in manager.GetInhibitors());guard=ScreenLockGuard();owned=[]
try:
    guard.start();assert guard.active();assert manager.IsInhibited(dbus.UInt32(8))
    after=set(str(p) for p in manager.GetInhibitors())
    for path in after-before:
        item=dbus.Interface(inspector.get_object(ScreenLockGuard.SERVICE,path),'org.gnome.SessionManager.Inhibitor')
        if str(item.GetAppId())=='Kalshi Bitcoin Bot':
            assert int(item.GetFlags())==8;owned.append(path)
    assert owned
finally:guard.close()
remaining=set(str(p) for p in manager.GetInhibitors());assert not set(owned)&remaining
inspector.close()
print(json.dumps({'result':'passed','session_idle_inhibitor_verified':True,'released_on_close':True,'global_settings_changed':False,'manual_lock_or_unlock_attempted':False,'account_connected':False}))
