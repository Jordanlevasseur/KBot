"""Hidden GUI persistence/launch wiring check using disposable settings only."""
import json
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from kalshi_bot.desktop import Application
from kalshi_bot.desktop_runtime import sandbox_command

with tempfile.TemporaryDirectory() as directory:
    base=Path(directory)
    with patch('kalshi_bot.desktop.paths',return_value=(base/'state',base/'config')):
        root=tk.Tk();root.withdraw();app=Application(root,autostart=False)
        assert float(app.target_buy.get())==30 and float(app.target_exit.get())==19
        app.target_buy.set('75.5');app.target_exit.set('40.25')
        app.apply_strategy()
        deadline=time.monotonic()+5
        while app.strategy_saving and time.monotonic()<deadline:
            root.update();time.sleep(.01)
        assert not app.strategy_saving
        saved=json.loads(app.strategy_path.read_text())
        assert saved['target_buy_usd']==75.5 and saved['target_exit_usd']==40.25
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        widgets=list(descendants(root))
        targets=next(w for w in widgets if w.winfo_class()=='TLabel' and w.cget('text')=='BITCOIN TARGET DISTANCE')
        volatility=next(w for w in widgets if w.winfo_class()=='TLabel' and w.cget('text')=='VOLATILITY LIMITS')
        target_row=targets.master;vol_row=volatility.master
        assert target_row.master is vol_row.master
        packed=target_row.master.pack_slaves()
        assert packed.index(target_row)==packed.index(vol_row)+1
        app.closing=True;root.destroy()
        root=tk.Tk();root.withdraw();app=Application(root,autostart=False)
        assert float(app.target_buy.get())==75.5 and float(app.target_exit.get())==40.25
        command=sandbox_command(Path(__file__).resolve().parent.parent,base/'state','simulation',target_buy_usd=float(app.target_buy.get()),target_exit_usd=float(app.target_exit.get()))
        assert command[command.index('--target-buy-usd')+1]=='75.5'
        assert command[command.index('--target-exit-usd')+1]=='40.25'
        app.closing=True;root.destroy()
print('PASS: target settings row beneath volatility; Save/reopen persists both values; launcher forwards both values.')
