"""Real Tk regression: all tabs and both Linux wheel directions; no worker."""
import os
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from kalshi_bot.desktop import Application

with tempfile.TemporaryDirectory() as directory:
    os.environ['XDG_STATE_HOME']=directory+'/state'
    os.environ['XDG_CONFIG_HOME']=directory+'/config'
    root=tk.Tk();app=Application(root,autostart=False)
    app.pending_activity=[('audit','Known order absent: synthetic historical canceled order')]*30
    app.flush_activity();root.update();start=time.monotonic();events=0
    tabs=app.tabs.tabs()
    for tab in tabs:
        app.tabs.select(tab);root.update()
        frame=root.nametowidget(tab)
        widgets=[frame]
        for widget in widgets:
            widgets.extend(widget.winfo_children())
            for sequence in ('<Button-4>','<Button-5>','<MouseWheel>'):
                widget.event_generate(sequence,**({'delta':-120} if sequence=='<MouseWheel>' else {}))
                events+=1
        root.update()
    root.after(50,app.close)
    root.mainloop()
    print({'tabs':len(tabs),'wheel_events':events,'seconds':round(time.monotonic()-start,3),'closed':True})
