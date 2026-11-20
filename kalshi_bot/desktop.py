"""Lightweight native desktop application; no browser or local web server."""
import argparse
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler,QueueHandler,QueueListener
import os
from pathlib import Path
import queue
import stat
import sys
import time
import subprocess
import threading
import pwd
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
from .desktop_runtime import Worker,paths,sandbox_command,gateway_health,session_badge
from .power import KeepAwake,PowerUnavailable,ScreenLockGuard,PowerMonitor
from .profits import ProfitReader,profit_text,trade_time_text
from .clocks import Countdown
from .strategy import ENTRY_WINDOW_SECONDS,ENTRY_CUTOFF_SECONDS,StrategyPolicy

ROOT=Path(__file__).resolve().parent.parent
BG='#101827'; PANEL='#192537'; CARD='#213047'; TEXT='#edf4ff'; MUTED='#a8b7cd'; ACCENT='#62e0bd'
LABELS={'Simulation • offline':'simulation','Demo account • read only':'demo-monitor',
        'Live account • read only':'production-monitor','Demo trading • test funds':'demo-trading',
        'Live trading • real money':'production-trading'}


def dollars(cents):
    if cents is None: return '—'
    try: return f'${float(cents)/100:,.2f}'
    except (TypeError,ValueError): return '—'


def cents(value):
    if value is None: return '—'
    try: return f'{float(value):.2f}¢'
    except (TypeError,ValueError): return '—'


def within_widget(widget,ancestor):
    """A root's empty parent name resolves to itself in Tk; stop there."""
    seen=set()
    while widget is not None and id(widget) not in seen:
        if widget is ancestor:return True
        seen.add(id(widget))
        try:
            parent=widget.winfo_parent()
            if not parent:return False
            widget=widget.nametowidget(parent)
        except (tk.TclError,KeyError,AttributeError):return False
    return False


def event_text(value):
    if value.startswith('Decision failure: '):
        try:
            error=json.loads(value[len('Decision failure: '):])
            contract=(' • '+error['side'].upper()) if error.get('side') in ('yes','no') else ''
            limit=(' at '+cents(float(error['limit'])*100)) if contract and error.get('limit') is not None else ''
            return error.get('stage','Execution')+contract+limit+' • '+error.get('outcome','blocked')+' • '+error.get('reason','unknown reason')
        except (ValueError,TypeError):return 'Execution failure recorded'
    if not value.startswith('Decision: '): return value
    try: decision=json.loads(value[len('Decision: '):])
    except (ValueError,TypeError): return 'Trading decision recorded'
    pieces=[decision.get('ticker','Bitcoin'),decision.get('status','Waiting')]
    for side in ('yes','no'):
        if decision.get(side+'_ask') is not None:
            pieces.append(side.upper()+' buy '+cents(float(decision[side+'_ask'])*100))
    if decision.get('limit') is not None:
        pieces.append(str(decision.get('side','')).upper()+' limit '+cents(float(decision['limit'])*100))
    return ' • '.join(pieces)


class Application:
    def __init__(self,root,*,autostart=True):
        self.root=root
        self.state_root,self.config_root=paths()
        self.state_root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.config_root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.strategy_path=self.config_root/'strategy.json'
        self.strategy_values={'buy_min_cents':96,'sell_cents':92,'entry_window_seconds':240,'entry_cutoff_seconds':30,'max_volatility_pct':100.0}
        try:
            raw=json.loads(self.strategy_path.read_text())
            saved=StrategyPolicy(raw['buy_min_cents'],raw['sell_cents'],
                                 raw.get('entry_window_seconds',240),raw.get('entry_cutoff_seconds',30),
                                 raw.get('max_volatility_pct',100.0),raw.get('max_volatility_5m_pct',100.0),
                                 raw.get('max_shock_1m_pct',100.0))
            self.strategy_values={'buy_min_cents':saved.buy_min_cents,'sell_cents':saved.sell_cents,
                                  'entry_window_seconds':saved.entry_window_seconds,'entry_cutoff_seconds':saved.entry_cutoff_seconds,
                                  'max_volatility_pct':saved.max_volatility_pct,'max_volatility_5m_pct':saved.max_volatility_5m_pct,
                                  'max_shock_1m_pct':saved.max_shock_1m_pct}
        except FileNotFoundError:pass
        except (OSError,ValueError,KeyError,TypeError):pass
        self.ui_jobs=queue.Queue()
        self.power_messages=queue.Queue()
        self.power=None
        self.power_ready=True
        self.power_pause_pending=False
        self.events=queue.Queue()
        self.worker=Worker(self.events)
        self.profits=ProfitReader(self.events);self.profit_key=None;self.profit_requested_at=0;self.profit_values={};self.trade_times={}
        self.root.bind('<Destroy>',lambda event:self.profits.close() if event.widget==self.root else None,add='+')
        self.data={}
        self.market_data={}
        self.execution_at=0
        self.table_rows={}
        self.last_state_at=None
        self.state_blocked=False
        self.countdown=Countdown()
        self.preflighting=False
        self.closing=False
        self.install_process=None
        self.switching=False
        self.switching_mode='simulation'
        self.strategy_saving=False
        self.mode=tk.StringVar(value=next(iter(LABELS)))
        self.connection=tk.StringVar(value='Starting isolated worker…')
        self.refresh_status=tk.StringVar(value='Waiting for market data…')
        self.market_stamp=tk.StringVar(value='Market data: waiting for first snapshot')
        self.selection_status=tk.StringVar(value='Selected side: None • Automatic purchases: paused')
        self.eligibility_status=tk.StringVar(value='Price eligibility: waiting for market data')
        self.strategy_status=tk.StringVar(value='Strategy checks: waiting')
        self.buy_quotes=tk.StringVar(value='YES • BUY — / SELL —     |     NO • BUY — / SELL —')
        self.btc_reference=tk.StringVar(value='BTC target: Unavailable • BRTI: Unavailable • Difference: —')
        self.market_volatility=tk.StringVar(value='30-second volatility: —')
        self.market_volatility_detail=tk.StringVar(value='Market range: — • High: — • Low: — • 1m/5m/15m: — / — / —')
        self.cash_label=tk.StringVar(value='AVAILABLE CASH')
        self.notice=tk.StringVar(value='Simulation uses synthetic prices and simulated money. No account is connected.')
        self.variables={key:tk.StringVar(value='—') for key in ('cash','pnl','buys','market','time','position','entry','bid','stop','orders')}
        self.fee=tk.StringVar(value='')
        self.buy_min=tk.StringVar(value=str(self.strategy_values['buy_min_cents']))
        self.sell_stop=tk.StringVar(value=str(self.strategy_values['sell_cents']))
        self.entry_window=tk.StringVar(value=str(self.strategy_values['entry_window_seconds']))
        self.entry_cutoff=tk.StringVar(value=str(self.strategy_values['entry_cutoff_seconds']))
        self.max_volatility=tk.StringVar(value=str(self.strategy_values['max_volatility_pct']))
        self.max_volatility_5m=tk.StringVar(value=str(self.strategy_values.get('max_volatility_5m_pct',100.0)))
        self.max_shock_1m=tk.StringVar(value=str(self.strategy_values.get('max_shock_1m_pct',100.0)))
        self.strategy_status_ui=tk.StringVar(value='')
        self.prepared_status=tk.StringVar(value='')
        self.power_status=tk.StringVar(value='')
        self.speed=tk.StringVar(value='1×')
        self.setup_status=tk.StringVar(value='Credentials stay on this laptop. Nothing is sent until you choose Connect.')
        self.log=logging.getLogger('kalshi.desktop')
        self.log.setLevel(logging.INFO)
        self.log.propagate=False
        handler=RotatingFileHandler(self.state_root/'desktop.log',maxBytes=1_000_000,backupCount=2)
        handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
        log_queue=queue.SimpleQueue()
        self.log.addHandler(QueueHandler(log_queue))
        self.log_listener=QueueListener(log_queue,handler)
        self.log_listener.start()
        self.build()
        self.root.protocol('WM_DELETE_WINDOW',self.close)
        self.root.report_callback_exception=self.callback_error
        self.root.after(100,self.poll)
        if autostart: self.root.after(200,self.connect)

    def build(self):
        root=self.root
        root.title('Kalshi Bitcoin Bot')
        root.geometry('1080x760')
        root.minsize(880,650)
        root.configure(bg=BG)
        style=ttk.Style(root)
        style.theme_use('clam')
        style.configure('.',font=('DejaVu Sans',10),background=PANEL,foreground=TEXT)
        style.configure('TFrame',background=BG)
        style.configure('Panel.TFrame',background=PANEL)
        style.configure('TLabel',background=PANEL,foreground=TEXT)
        style.configure('Muted.TLabel',foreground=MUTED)
        style.configure('Strategy.TLabel',background=PANEL,foreground='black')
        style.configure('Title.TLabel',font=('DejaVu Sans',22,'bold'),background=BG)
        style.configure('TButton',padding=(14,9),background=CARD,foreground=TEXT)
        style.map('TButton',background=[('active','#324962')],foreground=[('disabled','#667890')])
        style.configure('Accent.TButton',background=ACCENT,foreground=BG,font=('DejaVu Sans',10,'bold'))
        style.map('Accent.TButton',background=[('active','#91f4d6')])
        style.configure('TNotebook',background=BG,borderwidth=0)
        style.configure('TNotebook.Tab',padding=(18,10),background=PANEL)
        style.map('TNotebook.Tab',background=[('selected',CARD)])
        style.configure('Treeview',background=PANEL,fieldbackground=PANEL,foreground=TEXT,rowheight=29,borderwidth=0)
        style.configure('Treeview.Heading',background=CARD,foreground=TEXT,padding=8)
        style.map('Treeview',background=[('selected','#365879')])
        style.configure('TCombobox',fieldbackground=CARD,foreground=TEXT,padding=6)
        style.map('TCombobox',fieldbackground=[('readonly',CARD)],foreground=[('readonly',TEXT)])
        shell=ttk.Frame(root,padding=22)
        shell.pack(fill='both',expand=True)
        header=ttk.Frame(shell)
        header.pack(fill='x',pady=(0,16))
        ttk.Label(header,text='₿  BITCOIN / 15 MIN',style='Title.TLabel').pack(side='left')
        self.session_badge=tk.Label(header,text='SIMULATION',bg='#3b3040',fg='#f4bd93',padx=12,pady=8,font=('DejaVu Sans',9,'bold'))
        self.session_badge.pack(side='right')
        bar=ttk.Frame(shell,style='Panel.TFrame',padding=12)
        bar.pack(fill='x',pady=(0,14))
        selector=ttk.Combobox(bar,textvariable=self.mode,values=list(LABELS),state='readonly',width=32)
        selector.pack(side='left')
        selector.bind('<<ComboboxSelected>>',self.change_mode)
        self.connect_button=ttk.Button(bar,text='Reconnect',command=self.connect)
        self.connect_button.pack(side='left',padx=10)
        ttk.Button(bar,text='Refresh now',command=self.refresh).pack(side='right')
        ttk.Label(bar,textvariable=self.connection,style='Muted.TLabel').pack(side='left',padx=5)
        self.tabs=ttk.Notebook(shell)
        self.tabs.bind('<<NotebookTabChanged>>',lambda _ : self.render_history() if self.tabs.tab(self.tabs.select(),'text')=='Trade history' else None)
        self.tabs.pack(fill='both',expand=True)
        dashboard=ttk.Frame(self.tabs)
        self.tabs.add(dashboard,text='Dashboard')
        canvas=tk.Canvas(dashboard,bg=BG,highlightthickness=0)
        scroll=ttk.Scrollbar(dashboard,orient='vertical',command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y'); canvas.pack(side='left',fill='both',expand=True)
        overview=ttk.Frame(canvas,padding=(0,14))
        window=canvas.create_window((0,0),window=overview,anchor='nw')
        overview.bind('<Configure>',lambda _:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda e:canvas.itemconfigure(window,width=e.width))
        # Route two-finger touchpad scrolling (and Linux wheel events) to the
        # dashboard canvas instead of making the pointer activate controls.
        def dashboard_wheel(event):
            if not within_widget(event.widget,canvas):return
            if getattr(event,'num',None)==4: units=-3
            elif getattr(event,'num',None)==5: units=3
            else: units=-int(event.delta/120) if event.delta else 0
            if units: canvas.yview_scroll(units,'units')
            return 'break'
        for sequence in ('<MouseWheel>','<Button-4>','<Button-5>'):
            root.bind_all(sequence,dashboard_wheel,add='+')
        stats=ttk.Frame(overview)
        stats.pack(fill='x',pady=(0,14))
        for col,(key,label) in enumerate((('cash','AVAILABLE CASH'),('pnl','REALIZED P/L'),('buys','BUYS THIS MARKET'))):
            card=ttk.Frame(stats,style='Panel.TFrame',padding=16)
            card.grid(row=0,column=col,sticky='nsew',padx=(0,12) if col<2 else 0)
            stats.columnconfigure(col,weight=1)
            ttk.Label(card,**({'textvariable':self.cash_label} if key=='cash' else {'text':label}),style='Muted.TLabel',font=('DejaVu Sans',9)).pack(anchor='w')
            ttk.Label(card,textvariable=self.variables[key],font=('DejaVu Sans',24,'bold')).pack(anchor='w',pady=(8,0))
        market=ttk.Frame(overview,style='Panel.TFrame',padding=18)
        market.pack(fill='x',pady=(0,12))
        ttk.Label(market,text='CURRENT MARKET',style='Muted.TLabel',font=('DejaVu Sans',9)).grid(row=0,column=0,sticky='w')
        ttk.Label(market,textvariable=self.variables['market'],font=('DejaVu Sans',13,'bold')).grid(row=1,column=0,sticky='w',pady=8)
        ttk.Label(market,textvariable=self.variables['time'],font=('DejaVu Sans',21,'bold'),foreground=ACCENT).grid(row=0,column=1,rowspan=2,sticky='e',padx=12)
        market.columnconfigure(0,weight=1)
        self.progress=ttk.Progressbar(market,maximum=900)
        self.progress.grid(row=2,column=0,columnspan=2,sticky='ew')
        ttk.Label(market,textvariable=self.buy_quotes,font=('DejaVu Sans',10,'bold')).grid(row=3,column=0,columnspan=2,sticky='w',pady=(8,0))
        ttk.Label(market,textvariable=self.market_volatility,style='Muted.TLabel',font=('DejaVu Sans',9)).grid(row=4,column=0,columnspan=2,sticky='w',pady=(5,0))
        ttk.Label(market,textvariable=self.market_volatility_detail,style='Muted.TLabel',font=('DejaVu Sans',9),wraplength=900).grid(row=5,column=0,columnspan=2,sticky='w',pady=(2,0))
        ttk.Label(market,textvariable=self.refresh_status,style='Muted.TLabel',font=('Deja Vu Sans',9),wraplength=900).grid(row=6,column=0,columnspan=2,sticky='w',pady=(5,0))
        ttk.Label(market,textvariable=self.market_stamp,style='Muted.TLabel',font=('DejaVu Sans',9)).grid(row=7,column=0,columnspan=2,sticky='w',pady=(5,0))
        ttk.Label(market,textvariable=self.btc_reference,foreground='#FFFFFF',font=('DejaVu Sans',12,'bold'),wraplength=900).grid(row=8,column=0,columnspan=2,sticky='w',pady=(5,0))
        positions=ttk.Frame(overview,style='Panel.TFrame',padding=18)
        positions.pack(fill='x')
        for i,(key,label) in enumerate((('position','POSITION'),('entry','ENTRY PRICE'),('bid','POSITION SELL BID'),('stop','STOP-LOSS'),('orders','ACTIVE ORDERS'))):
            ttk.Label(positions,text=label,style='Muted.TLabel',font=('DejaVu Sans',9)).grid(row=0,column=i,sticky='w',padx=(0,14))
            ttk.Label(positions,textvariable=self.variables[key],font=('DejaVu Sans',11,'bold'),wraplength=220 if key=='stop' else 0).grid(row=1,column=i,sticky='nw',pady=(10,0))
            positions.columnconfigure(i,weight=1)
        ttk.Label(overview,textvariable=self.selection_status,style='Muted.TLabel',wraplength=940).pack(fill='x',pady=(6,0))
        ttk.Label(overview,textvariable=self.eligibility_status,style='Muted.TLabel',wraplength=940).pack(fill='x',pady=(4,0))
        ttk.Label(overview,textvariable=self.strategy_status,style='Muted.TLabel',wraplength=940).pack(fill='x',pady=(4,0))
        ttk.Label(overview,textvariable=self.prepared_status,style='Muted.TLabel',wraplength=940).pack(fill='x',pady=(4,0))
        ttk.Label(overview,textvariable=self.power_status,style='Muted.TLabel',wraplength=940).pack(fill='x',pady=(4,0))
        controls=ttk.Frame(overview)
        controls.pack(fill='x',pady=18)
        self.start_button=ttk.Button(controls,text='Start simulation',style='Accent.TButton',command=self.start)
        self.start_button.pack(side='left')
        self.pause_button=ttk.Button(controls,text='Pause new buys',command=self.pause)
        self.pause_button.pack(side='left',padx=10)
        speed=ttk.Combobox(controls,textvariable=self.speed,values=['1×','5×','15×'],width=5,state='readonly')
        speed.pack(side='right')
        speed.bind('<<ComboboxSelected>>',lambda _:self.worker.send('speed',value=int(self.speed.get()[:-1])) if self.current_mode()=='simulation' else None)
        ttk.Label(controls,text='Replay speed',background=BG,style='Muted.TLabel').pack(side='right',padx=8)
        rules=ttk.Frame(overview,style='Panel.TFrame',padding=14)
        rules.pack(fill='x')
        ttk.Label(rules,text='TRADING STRATEGY',font=('DejaVu Sans',10,'bold')).pack(anchor='w')
        strategy_row=ttk.Frame(rules)
        strategy_row.pack(fill='x',pady=(8,2))
        ttk.Label(strategy_row,text='Buy threshold (¢):',style='Strategy.TLabel').pack(side='left')
        ttk.Spinbox(strategy_row,from_=50,to=99,textvariable=self.buy_min,width=5,foreground='black').pack(side='left',padx=(6,16))
        ttk.Label(strategy_row,text='Protective sell trigger/limit (¢):',style='Strategy.TLabel').pack(side='left')
        ttk.Spinbox(strategy_row,from_=1,to=98,textvariable=self.sell_stop,width=5,foreground='black').pack(side='left',padx=6)
        timing_row=ttk.Frame(rules)
        timing_row.pack(fill='x',pady=(6,2))
        ttk.Label(timing_row,text='Entry starts (seconds before close):',style='Strategy.TLabel').pack(side='left')
        ttk.Spinbox(timing_row,from_=60,to=900,textvariable=self.entry_window,width=5,foreground='black').pack(side='left',padx=6)
        ttk.Label(timing_row,text='Block buys in final (seconds):',style='Strategy.TLabel').pack(side='left',padx=(16,0))
        ttk.Spinbox(timing_row,from_=0,to=899,textvariable=self.entry_cutoff,width=5,foreground='black').pack(side='left',padx=6)
        volatility_row=ttk.Frame(rules)
        volatility_row.pack(fill='x',pady=(6,2))
        ttk.Label(volatility_row,text='VOLATILITY LIMITS',style='Strategy.TLabel').pack(side='left')
        ttk.Label(volatility_row,text='15m limit (%):',style='Strategy.TLabel').pack(side='left',padx=(18,0))
        ttk.Entry(volatility_row,textvariable=self.max_volatility,width=7,foreground='black').pack(side='left',padx=6)
        ttk.Label(volatility_row,text='5m limit (%):',style='Strategy.TLabel').pack(side='left',padx=(14,0))
        ttk.Entry(volatility_row,textvariable=self.max_volatility_5m,width=7,foreground='black').pack(side='left',padx=6)
        ttk.Label(volatility_row,text='1m shock (%):',style='Strategy.TLabel').pack(side='left',padx=(14,0))
        ttk.Entry(volatility_row,textvariable=self.max_shock_1m,width=7,foreground='black').pack(side='left',padx=6)
        save_row=ttk.Frame(rules)
        save_row.pack(fill='x',pady=(8,0))
        ttk.Button(save_row,text='Save settings',command=self.apply_strategy).pack(side='left')
        ttk.Label(rules,textvariable=self.strategy_status_ui,style='Muted.TLabel').pack(anchor='w',pady=(2,4))
        ttk.Label(rules,text=f'Buy limits range from your threshold through 99¢. Entry: final {ENTRY_WINDOW_SECONDS//60} minutes; no new buys in the last {ENTRY_CUTOFF_SECONDS} seconds. A protective limit sell can remain unfilled if there are no buyers at that price.',style='Muted.TLabel',wraplength=920).pack(anchor='w',pady=(2,0))
        ttk.Label(rules,text='Whole contracts: available Bitcoin cash ÷ $2, rounded down. Fees are reserved separately; one position at a time.',style='Muted.TLabel',wraplength=920).pack(anchor='w',pady=(6,0))
        self.history=self.table_tab('Trade history',('time','id','market','action','side','quantity','price','fee','profit'),(200,65,195,65,50,65,75,65,115),
            hint='Net profit or loss for each BUY fill, including fees. Losses are negative. TIME is the confirmed fill date/time in your local timezone; unavailable times show —.')
        self.history.heading('time',text='TIME (LOCAL)')
        self.history.heading('profit',text='PROFIT / LOSS')
        self.history.tag_configure('loss',foreground='#ff9898')
        self.activity=self.table_tab('Activity log',('type','details'),(120,790))
        self.setup_tab=ttk.Frame(self.tabs,padding=22)
        self.tabs.add(self.setup_tab,text='Account setup')
        ttk.Label(self.setup_tab,text='Your key stays on this laptop',style='Title.TLabel',font=('DejaVu Sans',18,'bold')).pack(anchor='w')
        text=('Simulation works immediately without an account. Account modes connect only after your explicit confirmation.\n\n'
              'Trading monitors sequenced order-book streams. Network interruptions can delay observations and exits.\n'
              'Live trading needs an explicit connection and a separate Start confirmation. It uses real money.\n\n'
              'Use a dedicated revocable Kalshi key. Never paste a private key into chat. Configure a local PEM file below.\n'
              'Use Install connection service below. Your laptop will ask for administrator authentication; no key is sent during installation.')
        ttk.Label(self.setup_tab,text=text,background=BG,wraplength=900,justify='left').pack(anchor='w',pady=20)
        buttons=ttk.Frame(self.setup_tab)
        buttons.pack(fill='x')
        ttk.Button(buttons,text='Configure demo key…',command=lambda:self.configure_key('demo')).pack(side='left')
        ttk.Button(buttons,text='Configure production API key…',command=lambda:self.configure_key('production')).pack(side='left',padx=10)
        ttk.Button(self.setup_tab,text='Install / repair connection service…',command=self.install_gateway).pack(anchor='w',pady=(16,0))
        ttk.Button(self.setup_tab,text='Check credentials offline',command=self.preflight).pack(anchor='w',pady=(16,8))
        row=ttk.Frame(self.setup_tab)
        row.pack(fill='x',pady=12)
        ttk.Label(row,text='Optional extra fee reserve ($/contract; blank = automatic):',background=BG).pack(side='left')
        ttk.Entry(row,textvariable=self.fee,width=12).pack(side='left',padx=10)
        ttk.Label(self.setup_tab,textvariable=self.setup_status,background=BG,wraplength=900,foreground=ACCENT).pack(anchor='w',pady=12)
        ttk.Button(self.setup_tab,text='Read setup and acceptance guide',command=self.guide).pack(anchor='w')
        ttk.Label(shell,textvariable=self.notice,style='Muted.TLabel',background=BG,wraplength=990,justify='left').pack(fill='x',pady=(12,0))

    def table_tab(self,label,columns,widths,hint=None):
        frame=ttk.Frame(self.tabs,padding=(0,14))
        self.tabs.add(frame,text=label)
        if hint:ttk.Label(frame,text=hint,style='Muted.TLabel',wraplength=940).pack(side='top',fill='x',pady=(0,8))
        tree=ttk.Treeview(frame,columns=columns,show='headings')
        for column,width in zip(columns,widths):
            tree.heading(column,text=column.upper())
            tree.column(column,width=width,minwidth=50,stretch=column in ('market','details'))
        if label=='Trade history':
            horizontal=ttk.Scrollbar(frame,orient='horizontal',command=tree.xview)
            tree.configure(xscrollcommand=horizontal.set)
            horizontal.pack(side='bottom',fill='x')
        scrollbar=ttk.Scrollbar(frame,orient='vertical',command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side='left',fill='both',expand=True)
        scrollbar.pack(side='right',fill='y')
        return tree

    def background(self, work, done):
        # No Tk calls (including after) from worker threads.
        def run():
            try: result=(True,work())
            except Exception as exc: result=(False,exc)
            self.ui_jobs.put((done,result))
        threading.Thread(target=run,daemon=True).start()

    def current_mode(self): return LABELS[self.mode.get()]

    def credential_directory(self,mode=None):
        mode=mode or self.current_mode()
        environment='production' if mode.startswith('production') else 'demo'
        return self.config_root/environment

    def connect(self,*,preflight=False):
        if self.closing or self.switching or not self.power_ready or self.strategy_saving: return
        mode=self.current_mode()
        if mode!='simulation' and not self.credential_directory().is_dir():
            messagebox.showinfo('Configure a local API key','Open Account setup and configure the selected environment first.',parent=self.root)
            self.tabs.select(self.setup_tab)
            return
        if mode!='simulation' and not preflight:
            if mode=='production-trading':
                description='Connect to your REAL account and resume protective exits for existing bot positions? New buys stay paused until you separately click Start live trading. This software has not been tested against your account.'
            else:
                description='This will read your real Kalshi account. It cannot submit orders.' if mode.startswith('production') else ('This connects to demo funds; demo entries need Start. It is not production validated.' if mode=='demo-trading' else 'Read-only demo account connection.')
            if not messagebox.askyesno('Connect to Kalshi?',description,parent=self.root): return
        if mode!='simulation' and not preflight and not (Path('/run/kalshi-'+mode.split('-')[0])/'gateway.sock').is_socket():
            messagebox.showinfo('Connection service needed','Install the selected environment’s connection service in Account setup, then connect. No insecure network fallback is used.',parent=self.root)
            self.tabs.select(self.setup_tab)
            return
        values=tuple(v.get() for v in (self.buy_min,self.sell_stop,self.entry_window,self.entry_cutoff,self.max_volatility,self.max_volatility_5m,self.max_shock_1m,self.fee))
        credentials=self.credential_directory(mode)
        self.switching_mode=mode
        self.switching=True
        self.connection.set('Checking Linux isolation…')
        def prepare():
            if mode!='simulation' and not preflight:
                health=gateway_health(mode.split('-')[0])
                if mode.endswith('-trading') and (not health['trading_enabled'] or not health['streaming_enabled']):
                    raise ValueError('Trading gateway upgrade required')
                if mode.endswith('-trading') and health.get('stream_protocol',0)<2:
                    raise ValueError('Verified-heartbeat gateway upgrade required')
                if mode.endswith('-trading') and health.get('entry_window_seconds')!=ENTRY_WINDOW_SECONDS:
                    raise ValueError('Gateway entry-window update required')
                if mode.endswith('-trading') and health.get('market_balance_scope')!=1:
                    raise ValueError('Bitcoin cash-scoping gateway update required')
                if mode.endswith('-trading') and health.get('strategy_policy_version')!=1:
                    raise ValueError('Configurable-strategy gateway update required')
            directory=self.state_root/mode
            directory.mkdir(parents=True,exist_ok=True,mode=0o700)
            strategy=StrategyPolicy(int(values[0]),int(values[1]),int(values[2]),int(values[3]),float(values[4]),float(values[5]),float(values[6]))
            command=sandbox_command(ROOT,directory,mode,credentials if mode!='simulation' else None,preflight,values[7] or None,
                                    strategy.buy_min_cents,strategy.sell_cents,strategy.entry_window_seconds,strategy.entry_cutoff_seconds,strategy.max_volatility_pct,strategy.max_volatility_5m_pct,strategy.max_shock_1m_pct)
            return command
        def prepared(result):
            self.switching=False
            if mode!=self.current_mode():return
            if not result[0]:
                self.notice.set('Connection setup failed. Check credentials, strategy settings and the matching gateway service.')
                return
            self.launch_prepared(result[1],preflight)
        self.background(prepare,prepared)

    def launch_prepared(self,command,preflight):
        if self.closing:return
        mode=self.current_mode()
        self.switching=True
        def launch_after_stop():
            self.events.put((self.worker.generation,{'type':'launch','command':command,'preflight':preflight}))
        self.worker.stop(callback=launch_after_stop)
        self.connection.set('Checking Linux isolation…')
        self.last_state_at=None
        self.state_blocked=False
        self.session_badge.configure(text=session_badge(mode))

    def change_mode(self,_=None):
        if self.switching:
            self.mode.set(next(label for label,value in LABELS.items() if value==self.switching_mode))
            return
        if self.data.get('position') and self.data.get('mode')!='simulation':
            if not messagebox.askyesno('Stop the current worker?','Switching stops position monitoring. Active exchange orders may remain. Continue?',parent=self.root):
                self.mode.set(next(label for label,value in LABELS.items() if value==self.data.get('mode','simulation')))
                return
        self.switching_mode=self.current_mode()
        self.switching=True
        self.worker.stop(callback=lambda:self.events.put((self.worker.generation,{'type':'mode-stopped'})))
        self.connection.set('Disconnected • no account connection')
        self.data={}
        self.reset_values()
        mode=self.current_mode()
        self.start_button.configure(text='Start simulation' if mode=='simulation' else ('Start live trading' if mode=='production-trading' else ('Start demo trading' if mode=='demo-trading' else 'Refresh account')),state='normal')
        self.connect_button.configure(text='Reconnect' if mode=='simulation' else 'Connect…',state='normal')
        self.pause_button.configure(text='Pause new buys' if mode in ('simulation','demo-trading','production-trading') else 'Pause polling')
        self.notice.set('Simulation is offline.' if mode=='simulation' else 'No connection yet. Choose Connect explicitly. Live orders require a separate Start confirmation. Keep this app running to monitor bot positions.')

    def reset_values(self):
        self.market_data={}
        self.execution_at=0
        self.last_state_at=None
        self.state_blocked=False
        self.session_badge.configure(text=session_badge(self.current_mode()))
        self.refresh_status.set('Waiting for market data…')
        self.market_stamp.set('Market data: waiting for first snapshot')
        self.selection_status.set('Selected side: None • Automatic purchases: paused')
        self.eligibility_status.set('Price eligibility: waiting for market data')
        self.strategy_status.set('Strategy checks: waiting')
        self.prepared_status.set('')
        self.buy_quotes.set('YES • BUY — / SELL —     |     NO • BUY — / SELL —')
        self.countdown.sync({})
        for var in self.variables.values(): var.set('—')
        self.history.delete(*self.history.get_children())
        self.activity.delete(*self.activity.get_children())
        self.table_rows.clear()
        self.pending_activity=[]

    def start(self):
        if self.switching or not self.power_ready or self.power_pause_pending or getattr(self,'strategy_saving',False): return
        if self.current_mode()=='production-trading' and self.worker.process:
            if not messagebox.askyesno('Start REAL-MONEY trading?', f'This enables automated buys targeting floor(available Bitcoin cash in dollars / 2) whole contracts per buy. Buy threshold: {self.buy_min.get()}¢ (limits through 99¢); protective trigger/limit: {self.sell_stop.get()}¢. The sell limit may not fill. One position at a time. Buys stop 30 seconds before market close. Start live trading now?',parent=self.root): return
        if not self.worker.send('start'):
            self.connect()
            self.notice.set('Connect the worker, then press Start. No orders started automatically.')

    def pause(self):
        if self.worker.send('pause'):
            self.notice.set('Pause requested. Protective exits continue while the worker remains open; wait for its confirmed status.')

    def apply_strategy(self):
        if self.strategy_saving:return
        try:configured=StrategyPolicy(int(self.buy_min.get()),int(self.sell_stop.get()),int(self.entry_window.get()),int(self.entry_cutoff.get()),float(self.max_volatility.get()),float(self.max_volatility_5m.get()),float(self.max_shock_1m.get()))
        except (ValueError,TypeError):
            messagebox.showerror('Invalid strategy','Enter whole cents: buy threshold 50–99¢ and protective sell trigger/limit below the buy threshold.',parent=self.root);return
        if self.data.get('running') or self.data.get('position') or self.data.get('active_orders'):
            messagebox.showwarning('Strategy change blocked','Pause entries and resolve all open positions and orders before changing thresholds.',parent=self.root);return
        self.strategy_saving=True
        def persist():
            self.config_root.mkdir(parents=True,exist_ok=True,mode=0o700)
            temp=self.strategy_path.with_suffix('.json.new')
            fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'w') as stream:
                json.dump({'buy_min_cents':configured.buy_min_cents,'sell_cents':configured.sell_cents,'entry_window_seconds':configured.entry_window_seconds,'entry_cutoff_seconds':configured.entry_cutoff_seconds,'max_volatility_pct':configured.max_volatility_pct,'max_volatility_5m_pct':configured.max_volatility_5m_pct,'max_shock_1m_pct':configured.max_shock_1m_pct},stream)
                stream.flush();os.fsync(stream.fileno())
            os.replace(temp,self.strategy_path);os.chmod(self.strategy_path,0o600)
        def persisted(result):
            self.strategy_saving=False
            if not result[0]:
                messagebox.showerror('Could not save strategy','The private local settings file could not be updated.',parent=self.root)
                return
            self.strategy_values={'buy_min_cents':configured.buy_min_cents,'sell_cents':configured.sell_cents,'entry_window_seconds':configured.entry_window_seconds,'entry_cutoff_seconds':configured.entry_cutoff_seconds,'max_volatility_pct':configured.max_volatility_pct,'max_volatility_5m_pct':configured.max_volatility_5m_pct,'max_shock_1m_pct':configured.max_shock_1m_pct}
            if self.worker.process:
                if not self.worker.send('strategy',buy_min_cents=configured.buy_min_cents,sell_cents=configured.sell_cents,entry_window_seconds=configured.entry_window_seconds,entry_cutoff_seconds=configured.entry_cutoff_seconds,max_volatility_pct=configured.max_volatility_pct,max_volatility_5m_pct=configured.max_volatility_5m_pct,max_shock_1m_pct=configured.max_shock_1m_pct):
                    self.strategy_status_ui.set('Saved for the next connection; the current worker did not accept the update.')
                else:self.strategy_status_ui.set('Applying strategy in the connected worker…')
            else:self.strategy_status_ui.set(f'Saved: buy from {configured.buy_min_cents}¢; protective trigger/limit {configured.sell_cents}¢.')

        self.background(persist,persisted)

    def refresh(self):
        if self.worker.send('refresh'):
            self.notice.set('Refresh requested. Trading and pause settings stay as they are.')
        else:
            self.notice.set('Choose Connect to refresh market and account data.')

    def preflight(self):
        if self.current_mode()=='simulation':
            messagebox.showinfo('Select an account mode','Select Demo account, Live account, or Demo trading first. Offline checks do not contact Kalshi.',parent=self.root)
            return
        self.connect(preflight=True)

    def install_gateway(self):
        mode=self.current_mode()
        environment='production' if mode.startswith('production') else 'demo'
        trading=mode.endswith('-trading')
        description=('Install the secure '+environment+' connection service'+(' with narrowly limited order submission enabled' if trading else ' for read-only access')+'? This creates a dedicated service user and firewall rules for that user only. The gateway will go online, but no account key is read and no order is placed. Your laptop may ask for an administrator password.')
        if not messagebox.askyesno('Install connection service?',description,parent=self.root): return
        command=['/usr/bin/pkexec','--disable-internal-agent','/usr/bin/python3','-B',str(ROOT/'deploy/setup-system.py'),
                 '--environment',environment,'--client-user',pwd.getpwuid(os.getuid()).pw_name]
        if trading: command+=['--allow-trading']
        self.setup_status.set('Administrator authentication / service installation in progress…')
        def install():
            try:
                self.install_process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                stdout,stderr=self.install_process.communicate(timeout=90)
                result=type('InstallResult',(),{'returncode':self.install_process.returncode,'stdout':stdout,'stderr':stderr})()
                okay=result.returncode==0
                # Installation logs contain no key material. Retain for diagnosis.
                log=self.state_root/'gateway-install.log'
                log.write_text(result.stdout+'\n'+result.stderr)
                os.chmod(log,0o600)
                message='Secure '+environment+' gateway is online. Configure your key and choose Connect.' if okay else 'Gateway installation did not complete. Check administrator authentication and gateway-install.log. No account connected.'
            except (OSError,subprocess.TimeoutExpired):
                if self.install_process is not None and self.install_process.poll() is None:
                    try:self.install_process.terminate()
                    except OSError:pass
                message='Administrator installation unavailable or timed out. No account connected. See the setup guide.'
            finally:
                self.install_process=None
            self.events.put((self.worker.generation,{'type':'setup-result','message':message}))
        threading.Thread(target=install,daemon=True).start()

    def configure_key(self,environment):
        window=tk.Toplevel(self.root)
        window.title('Configure '+environment+' API key locally')
        window.configure(bg=PANEL)
        window.transient(self.root); window.grab_set()
        window.geometry('660x330')
        box=ttk.Frame(window,style='Panel.TFrame',padding=22); box.pack(fill='both',expand=True)
        ttk.Label(box,text='Private key text is never displayed or logged.',style='Muted.TLabel').pack(anchor='w')
        ttk.Label(box,text='API key ID').pack(anchor='w',pady=(16,4))
        key_id=tk.StringVar()
        entry=ttk.Entry(box,textvariable=key_id,show='•',width=60); entry.pack(fill='x'); entry.focus_set()
        path=tk.StringVar(value='No file selected')
        ttk.Label(box,textvariable=path,wraplength=590).pack(anchor='w',pady=16)
        def browse():
            selected=filedialog.askopenfilename(parent=window,title='Select your downloaded private key',filetypes=[('Private key','*.pem *.key *.txt'),('All files','*')])
            if selected:
                try:
                    info=Path(selected).lstat()
                    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid():
                        raise ValueError('Untrusted key file')
                    if info.st_mode&0o077:
                        if not messagebox.askyesno('Protect this key file?', 'This key file can be read by other users. Make it private (owner read/write only) before using it?',parent=window): return
                        fd=os.open(selected,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
                        try:
                            actual=os.fstat(fd)
                            if not stat.S_ISREG(actual.st_mode) or actual.st_uid!=os.getuid(): raise ValueError('Invalid source')
                            os.fchmod(fd,0o600)
                        finally: os.close(fd)
                    path.set(selected)
                except (OSError,ValueError):
                    messagebox.showerror('Cannot use this file','Choose a regular private-key file owned by your user, not a symlink.',parent=window)
        ttk.Button(box,text='Choose local PEM file…',command=browse).pack(anchor='w')
        def save():
            destination=self.config_root/environment
            if destination.exists():
                messagebox.showerror('Credentials already configured','Existing credentials are not overwritten. Revoke/rotate them using the documented local setup procedure.',parent=window)
                return
            try:
                from .credentials import configure
                configure(destination,environment,key_id.get().strip(),path.get())
            except Exception:
                messagebox.showerror('Credential setup failed','Check that the source is your private Ed25519/RSA PEM file, owned by you with permissions 600. No key material was logged.',parent=window)
                return
            finally:
                key_id.set('')
            self.setup_status.set(environment.capitalize()+' credentials saved privately. No connection attempted.')
            window.destroy()
        ttk.Button(box,text='Validate and save locally',style='Accent.TButton',command=save).pack(anchor='e',pady=20)
        window.protocol('WM_DELETE_WINDOW',lambda:(key_id.set(''),window.destroy()))

    def guide(self):
        window=tk.Toplevel(self.root); window.title('Kalshi Bitcoin Bot — setup guide'); window.geometry('900x680')
        text=tk.Text(window,bg=PANEL,fg=TEXT,wrap='word',font=('DejaVu Sans',10),padx=20,pady=20)
        scroll=ttk.Scrollbar(window,command=text.yview); text.configure(yscrollcommand=scroll.set)
        text.pack(side='left',fill='both',expand=True); scroll.pack(side='right',fill='y')
        def read():
            return '\n\n'.join(file.read_text() for file in (ROOT/'docs'/name for name in ('ONLINE.md','DESKTOP.md','TRADING.md','ACCOUNT_SETUP.md')) if file.is_file())
        def loaded(result):
            if not window.winfo_exists():return
            text.insert('end',result[1] if result[0] else 'Setup guide unavailable.')
            text.configure(state='disabled')
        self.background(read,loaded)

    def update_state(self,state):
        revision=state.get('execution_at',0)
        if revision and revision<self.execution_at:
            state={**state,'running':self.data.get('running',False)}
        else:self.execution_at=max(self.execution_at,revision)
        self.data=state
        self.state_blocked=bool(state.get('blocked'))
        if not self.state_blocked:
            now=time.monotonic()
            sample=state.get('clock_sample_mono',now)
            self.last_state_at=sample if isinstance(sample,(int,float)) and 0<=now-sample else now
        mode=state.get('mode','simulation')
        healthy=mode!='simulation' and state.get('account_connected',
            state.get('connection','').lower().startswith('connected') or 'account connected' in state.get('connection','').lower()) and not self.state_blocked
        retrying=bool(state.get('auto_reconnecting')) and not self.state_blocked
        self.connect_button.configure(text='Connected ✓' if healthy else 'Retrying automatically…' if retrying else ('Reconnect' if mode=='simulation' else 'Reconnect…'),state='disabled' if healthy or retrying else 'normal')
        if mode in ('simulation','demo-trading','production-trading'):
            label='Start simulation' if mode=='simulation' else ('Start live trading' if mode=='production-trading' else 'Start demo trading')
            self.start_button.configure(text=('Simulation running' if mode=='simulation' else 'Trading enabled') if state.get('running') else label,state='disabled' if state.get('running') or self.state_blocked or not self.power_ready else 'normal')
        self.connection.set(state.get('connection','Unknown')+(' • entries started' if state.get('running') else ' • entries paused'))
        values=self.variables
        self.cash_label.set('AVAILABLE CASH' if mode=='simulation' else 'AVAILABLE FOR BITCOIN' if state.get('cash_scope')=='market' else 'TOTAL ACCOUNT CASH')
        values['cash'].set(dollars(state.get('cash_cents')))
        values['pnl'].set(dollars(state.get('pnl_cents')))
        values['buys'].set(str(state['buys'])+' completed' if state.get('buys') is not None else 'Read only')
        values['market'].set(state.get('market','—'))
        self.countdown.sync(state)
        self.render_clock()
        position=state.get('position')
        values['position'].set((str(position.get('qty',position.get('quantity','?')))+' '+str(position.get('side','')).upper()) if position else 'None')
        values['entry'].set(cents(state.get('entry_cents')))
        self.update_buy_quotes(state)
        value=state.get('current_volatility_30s_pct')
        self.market_volatility.set('30-second volatility: '+(format(value,'.2f')+'%' if isinstance(value,(int,float)) else '—'))
        def cents_value(key):
            value=state.get(key)
            return format(value,'.2f')+'¢' if isinstance(value,(int,float)) else '—'
        self.market_volatility_detail.set('Market range '+cents_value('market_range_cents')+
            ' • High '+cents_value('market_high_cents')+' • Low '+cents_value('market_low_cents')+
            ' • Window ranges 1m/5m/15m: '+cents_value('range_1m_cents')+' / '+cents_value('range_5m_cents')+' / '+cents_value('range_15m_cents')+
            ' • Avg: '+cents_value('avg_1m_cents')+' / '+cents_value('avg_5m_cents')+' / '+cents_value('avg_15m_cents')+
            ' • '+str(state.get('volatility_status','WARMING UP'))+' • '+str(state.get('sample_count',0))+' valid / '+str(state.get('missing_count',0))+' blank samples')
        protection=state.get('protective_exit') or {}
        values['stop'].set(protection.get('message') or (f"LATCHED • {state.get('sell_cents',self.strategy_values['sell_cents'])}¢" if state.get('stop') else ('Not managed' if 'monitor' in state.get('mode','') else 'Not triggered')))
        values['orders'].set(str(len(state.get('active_orders',[]))))
        self.update_entry_status()
        self.notice.set(state.get('notice',''))
        if state.get('prepared_entry'):
            prep=state['prepared_entry']
            self.prepared_status.set('Prepared orders: '+('READY' if prep.get('ready') else 'refreshing')+' • '+str(prep.get('reason') or 'Awaiting validation')+' • reserved '+dollars(prep.get('reserved_cents')))
        else:self.prepared_status.set('')
        self.request_profits(state)
        if self.tabs.tab(self.tabs.select(),'text')=='Trade history':
            self.render_history()
        rows=[]
        for row in state.get('events',[])[-100:]:
            stamp=time.strftime('%H:%M:%S',time.localtime(row['at']))+' • ' if row.get('at') else ''
            rows.append((row.get('kind','event'),stamp+event_text(row.get('detail',''))))
        self.pending_activity=rows
        if time.monotonic()-getattr(self,'_activity_redraw_at',0)>=1:
            self.flush_activity()
        if self.market_data and mode!='simulation': self.update_market(self.market_data)

    def flush_activity(self):
        self.update_table(self.activity,getattr(self,'pending_activity',[]))
        self._activity_redraw_at=time.monotonic()

    def request_profits(self,state):
        mode=state.get('mode','simulation');history=state.get('history',[])
        tickers=tuple(sorted({row.get('ticker',row.get('market_ticker','')) for row in history if row.get('ticker',row.get('market_ticker'))}))
        key=(mode,tuple((str(row.get('id',row.get('fill_id'))),str(row.get('qty',row.get('count_fp'))),str(row.get('fee',row.get('fee_cost')))) for row in history),str(state.get('position')))
        if key!=self.profit_key:self.profit_values={};self.trade_times={}
        changed=key!=self.profit_key;self.profit_key=key
        if mode not in ('simulation','demo-trading','production-trading') or not tickers:return
        if changed or time.monotonic()-self.profit_requested_at>=2:
            self.profit_requested_at=time.monotonic()
            name='desktop-simulation.sqlite3' if mode=='simulation' else 'desktop-trading.sqlite3'
            self.profits.request(key,self.worker.generation,mode,self.state_root/mode/name,tickers)

    def render_history(self):
        rows=[]
        for row in self.data.get('history',[]):
            if 'price' in row:
                price=cents(row['price']);fee=cents(row.get('fee'));quantity=row.get('qty','—')
            else:
                side=row.get('side',row.get('outcome_side','yes'))
                price=cents(float(row[side+'_price_dollars'])*100) if row.get(side+'_price_dollars') is not None else '—'
                fee=cents(float(row.get('fee_cost','0'))*100);quantity=row.get('count_fp','—')
            identity=row.get('id',row.get('fill_id','—'))
            trade_time=trade_time_text(row)
            if trade_time=='—':trade_time=trade_time_text(self.trade_times.get(str(identity)))
            rows.append((trade_time,identity,row.get('ticker',row.get('market_ticker','—')),row.get('action','Fill'),row.get('side',row.get('outcome_side','—')),quantity,price,fee,profit_text(self.profit_values.get(str(identity)))))
        self.update_table(self.history,rows)

    def update_table(self,tree,rows):
        if self.table_rows.get(tree)==rows: return
        view=tree.yview()
        tree.delete(*tree.get_children())
        for values in rows:
            tags=('loss',) if tree is self.history and str(values[-1]).startswith('-$') else ()
            tree.insert('','end',values=values,tags=tags)
        if view: tree.yview_moveto(view[0])
        self.table_rows[tree]=rows

    def update_buy_quotes(self,state):
        self.buy_quotes.set('     |     '.join(s.upper()+' • BUY '+cents(state.get(s+'_ask_cents'))+' / SELL '+cents(state.get(s+'_bid_cents')) for s in ('yes','no')))
        position=self.data.get('position')
        if not position:
            self.variables['bid'].set('No position')
        elif state.get('position_ticker')==position.get('ticker') and state.get('position_bid_side')==position.get('side'):
            self.variables['bid'].set(position['side'].upper()+' '+cents(state.get('position_bid_cents')))
        elif position.get('side') in ('yes','no') and position.get('ticker')==state.get('market',state.get('ticker',self.data.get('market'))):
            side=position['side']
            self.variables['bid'].set(side.upper()+' '+cents(state.get(side+'_bid_cents')))
        else:
            self.variables['bid'].set('—')

    def update_market(self,message):
        self.market_data=message
        value=message.get('market_range_cents')
        self.market_volatility.set('Current market range: '+(format(value,'.2f')+'¢' if isinstance(value,(int,float)) else 'WARMING UP'))
        def cv(key):
            v=message.get(key); return format(v,'.2f')+'¢' if isinstance(v,(int,float)) else '—'
        self.market_volatility_detail.set('High '+cv('market_high_cents')+' • Low '+cv('market_low_cents')+
            ' • 1m/5m/15m ranges: '+cv('range_1m_cents')+' / '+cv('range_5m_cents')+' / '+cv('range_15m_cents')+
            ' • averages: '+cv('avg_1m_cents')+' / '+cv('avg_5m_cents')+' / '+cv('avg_15m_cents')+
            ' • '+str(message.get('status','WARMING UP'))+' • '+str(message.get('sample_count',0))+' valid / '+str(message.get('missing_count',0))+' blank')
        decision=message.get('strategy_decision')
        if decision:
            gap=decision.get('actual_interval_ms')
            cadence=(' • last interval '+format(gap,'.0f')+' ms') if isinstance(gap,(int,float)) else ''
            checked=decision.get('decision_mono')
            age=time.monotonic()-checked if isinstance(checked,(int,float)) else None
            delayed=('DELAYED • last check '+format(age*1000,'.0f')+' ms ago • ') if age is not None and age>.5 else ''
            self.strategy_status.set(delayed+'Strategy check #'+str(decision.get('decision_count','—'))+': '+decision.get('reason','Waiting')+cadence)
        ticker=message.get('market')
        self.variables['market'].set(ticker or 'Discovering the next Bitcoin market…')
        self.countdown.sync(message)
        self.update_buy_quotes(message)
        if ticker!=self.data.get('market'): self.variables['buys'].set('0 completed')
        last=message.get('market_last_update')
        stamp=time.strftime('%H:%M:%S',time.localtime(last)) if isinstance(last,(int,float)) else 'not received'
        verified=message.get('market_verified_age')
        verification=(' • feed verified '+format(verified,'.1f')+'s ago') if isinstance(verified,(int,float)) else ''
        self.market_stamp.set('Market data: '+message.get('market_health','waiting')+' • last price change '+stamp+verification)
        if message.get('execution_at',0)>=self.execution_at and 'entries_enabled' in message:
            self.execution_at=message.get('execution_at',0)
            self.data['running']=message['entries_enabled']
        sides=message.get('eligible_sides',[])
        limits=message.get('eligible_limits_cents',{})
        eligible=', '.join(side.upper()+' ask '+cents(message.get(side+'_ask_cents'))+
                          (' → limit '+cents(limits[side]) if side in limits else '') for side in sides) or 'none'
        window='entry window open' if message.get('entry_window_open') else 'outside entry window'
        remaining=message.get('remaining')
        if not message.get('entry_window_open') and isinstance(remaining,(int,float)) and 0<remaining<=ENTRY_CUTOFF_SECONDS:
            window='new purchases blocked: final 30 seconds'
        self.eligibility_status.set('Price eligibility: '+eligible+' • '+window+' • '+message.get('execution_stage','Waiting for account checks'))
        if message.get('market_error'):
            self.eligibility_status.set(self.eligibility_status.get()+' • data: '+str(message['market_error']))
        if message.get('diagnostics_error') or message.get('diagnostics_dropped'):
            self.eligibility_status.set(self.eligibility_status.get()+' • diagnostic recording incomplete')
        self.update_entry_status()

    def update_entry_status(self):
        side=self.data.get('selected_side') or (self.data.get('position') or {}).get('side')
        enabled='enabled' if self.data.get('running') else 'paused'
        if self.state_blocked:enabled+=' • execution blocked'
        self.selection_status.set('Selected side: '+(side.upper() if side else 'None')+' • Automatic purchases: '+enabled)

    def render_clock(self):
        from .reference_display import reference_text
        now=time.monotonic()
        if now-getattr(self,'_btc_display_at',0)>=.5:
            self._btc_display_at=now
            text=reference_text(self.market_data or {},mono=now)
            if self.btc_reference.get()!=text:self.btc_reference.set(text)
        self.variables['time'].set(self.countdown.text())
        remaining=self.countdown.seconds()
        self.progress['value']=0 if remaining is None else max(0,900-remaining)
        mode=self.data.get('mode',self.current_mode())
        age=None if self.last_state_at is None else max(0,time.monotonic()-self.last_state_at)
        stale=(age is not None and age>(60 if self.market_data else 15))
        market_health=self.market_data.get('market_health') if self.market_data else None
        recovering=self.last_state_at is not None and (bool(self.data.get('auto_reconnecting')) or market_health in ('reconnecting','waiting for snapshot'))
        protecting=market_health=='saving stop'
        verified=self.market_data.get('market_verified_age') if self.market_data else None
        if isinstance(verified,(int,float)):
            elapsed=max(0,time.monotonic()-self.market_data.get('clock_sample_mono',time.monotonic()))
            if verified+elapsed>5 and not recovering:stale=True
        if self.market_data and market_health not in ('connected','reconnecting','waiting for snapshot','saving stop'):stale=True
        connected=self.last_state_at is not None and bool(self.data.get('account_connected',True)) and not self.state_blocked
        self.session_badge.configure(text=session_badge(mode,connected=connected,running=bool(self.data.get('running')),stale=stale,blocked=self.state_blocked,recovering=recovering,protecting=protecting))
        if age is not None:
            status=self.data.get('status','Market data')
            confirmed=self.data.get('last_api_success_mono')
            api_age=max(0,time.monotonic()-confirmed) if isinstance(confirmed,(int,float)) else age
            self.refresh_status.set(f'Last decision: {status} • API last confirmed {int(api_age)}s ago')
        if self.market_data:
            account='blocked' if self.state_blocked else self.data.get('connection_phase',('connected' if connected else 'connecting'))
            if self.data.get('auto_reconnecting'):account+=' automatically'
            self.connection.set('Account: '+account+' • Prices: '+self.market_data.get('market_health','waiting'))

    def _flush_market_display(self):
        self._market_display_scheduled = False
        pending = getattr(self, '_pending_market_display', None)
        self._pending_market_display = None
        if (pending is None or self.closing
                or pending[0] != self.worker.generation
                or self.worker.process is None):
            return
        self.update_market(pending[1])

    def poll(self):
        if self.closing: return
        self.root.after(50,self.poll)
        for _ in range(20):
            try: done,result=self.ui_jobs.get_nowait()
            except queue.Empty:break
            done(result)
        for _ in range(20):
            try: message=self.power_messages.get_nowait()
            except queue.Empty:break
            kind=message['type']
            if kind=='power-lost':
                self.power_ready=False
                self.power_pause_pending=True
                self.notice.set('Sleep protection lost. Pausing new entries; protective monitoring continues.')
            elif kind=='power-ready':
                self.power_ready=True
                if message['initial']:self.connect()
                else:self.notice.set('Sleep protection restored. New entries remain paused; review before Start.')
            elif kind=='power-failed':
                self.power_ready=False
                self.notice.set('Sleep protection unavailable. New entries blocked; protective monitoring continues while awake.')
            elif kind=='power-status':
                self.power_status.set('Sleep protection unavailable; new entries blocked.' if not self.power_ready else ('Power: sleep and automatic screen locking prevented' if message['okay'] else 'Sleep protected; automatic lock prevention unavailable; retrying.'))
        if self.power_pause_pending:
            if self.worker.send('pause') or self.worker.process is None:self.power_pause_pending=False
        deadline=time.monotonic()+.015
        for _ in range(100):
            if time.monotonic()>deadline:break
            try: generation,message=self.events.get_nowait()
            except queue.Empty: break
            if generation!=self.worker.generation: continue
            kind=message.get('type')
            if kind=='launch':
                self.switching=False
                self.preflighting=message['preflight']
                def launched(result):
                    self.switching=False
                    if not result[0]:self.notice.set('Worker unavailable. Check Linux isolation permissions.')
                self.switching=True
                command=message['command']
                def launch(command=command):
                    if self.closing:return
                    try:self.worker.launch(command)
                    finally:
                        if self.closing:self.worker.stop()
                self.background(launch,launched)
            elif kind=='mode-stopped':
                self.switching=False
                if self.current_mode()=='simulation': self.connect()
            elif kind=='state': self.update_state(message['state'])
            elif kind=='strategy':
                self.data['buy_min_cents']=message['buy_min_cents'];self.data['sell_cents']=message['sell_cents']
                self.strategy_status_ui.set(f"Active: buy from {message['buy_min_cents']}¢; sell {message['sell_cents']}¢; entry starts {message['entry_window_seconds']}s before close; blocks final {message['entry_cutoff_seconds']}s.")
            elif kind=='strategy-error':
                self.strategy_status_ui.set('Strategy update rejected: '+message.get('message','worker state changed'))
            elif kind=='profit-ready':
                if message['mode']==self.data.get('mode') and message['key']==self.profit_key:
                    self.profit_values=message['values'];self.trade_times=message.get('trade_times',{});self.render_history()
            elif kind=='market':
                # Coalesce display-only market updates to four per second.
                self._pending_market_display = (generation, message)
                if not getattr(self, '_market_display_scheduled', False):
                    self._market_display_scheduled = True
                    self.root.after(500, self._flush_market_display)
            elif kind=='quote':
                if not self.market_data and message.get('ticker')==self.data.get('market'):
                    self.update_buy_quotes(message)
            elif kind=='ready': self.connection.set('Worker ready • entries paused')
            elif kind=='preflight':
                self.setup_status.set(message['message']); self.connection.set('Offline preflight passed')
                self.notice.set(message['message'])
            elif kind=='setup-result':
                self.setup_status.set(message['message'])
                self.notice.set(message['message'])
            elif kind=='error':
                self.state_blocked=True
                self.start_button.configure(state='disabled')
                self.connect_button.configure(text='Reconnect…',state='normal')
                self.log.warning(message['message'])
                self.connection.set('Error • stale data / execution blocked')
                self.notice.set(message['message'])
                self.setup_status.set(message['message'])
            elif kind=='exit':
                self.data.update(running=False,account_connected=False,auto_reconnecting=False,connection_phase='disconnected')
                self.update_entry_status()
                self.worker.process=None
                self.market_data={}
                self.last_state_at=None
                self.state_blocked=False
                self.refresh_status.set('Worker stopped • data is no longer refreshing')
                self.market_stamp.set('Market data: stopped')
                self.connect_button.configure(text='Connect…' if self.current_mode()!='simulation' else 'Reconnect',state='normal')
                self.start_button.configure(state='normal')
                if not self.preflighting: self.connection.set('Worker stopped • entries disabled')
        if time.monotonic()-getattr(self,'_activity_redraw_at',0)>=1 and hasattr(self,'pending_activity'):
            self.flush_activity()
        self.render_clock()

    def callback_error(self,kind,value,trace):
        import traceback
        location = ' -> '.join(
            f'{Path(f.filename).name}:{f.lineno}:{f.name}'
            for f in traceback.extract_tb(trace)[-8:]
        )
        self.log.error('Desktop callback failed: %s at %s',kind.__name__,location)
        self.notice.set('A display action failed. No new trading action was enabled. See the local application log.')

    def close(self):
        if self.closing: return
        if self.data.get('position') and not messagebox.askyesno('Close the application?','Position monitoring stops when the worker closes. Existing exchange orders may remain active. Close anyway?',parent=self.root): return
        self.closing=True
        if self.power:self.power.close()
        if self.install_process is not None and self.install_process.poll() is None:
            try:self.install_process.terminate()
            except OSError:pass
        self.connection.set('Closing worker safely…')
        finished=[]
        deadline=time.monotonic()+15
        self.worker.stop(callback=lambda:finished.append(True))
        def wait():
            if finished or time.monotonic()>=deadline: self.root.destroy()
            else: self.root.after(100,wait)
        wait()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--smoke-test',action='store_true',help='Create and exercise the GUI without a worker or account')
    p.add_argument('--screenshot',help='Optional screenshot path for GUI smoke-test QA')
    a=p.parse_args()
    os.umask(0o077)
    try: root=tk.Tk()
    except tk.TclError:
        print('A graphical desktop session is required. Run from your Applications menu or use the terminal simulator.',file=sys.stderr)
        return 1
    state,_=paths(); state.mkdir(parents=True,exist_ok=True,mode=0o700)
    lock=open(state/'desktop.lock','a')
    try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        messagebox.showinfo('Kalshi Bitcoin Bot','The application is already running.',parent=root)
        root.destroy(); return 0
    try:
        app=Application(root,autostart=False)
        if not a.smoke_test:
            app.power_ready=False
            app.power=PowerMonitor(app.power_messages)
            app.power.start()
        if a.smoke_test:
            sample={'mode':'simulation','connection':'Offline simulation','running':False,'cash_cents':100000,'pnl_cents':0,
                    'market':'KXBTC15M-SIM-0','remaining':120,'buys':0,'position':None,'entry_cents':None,'bid_cents':94,
                    'stop':False,'active_orders':[],'history':[],'events':[{'kind':'ready','detail':'Simulation ready. No account connected.'}],
                    'notice':'Simulated prices and money. No account connected.'}
            app.update_state(sample)
            root.update()
            assert app.variables['cash'].get()=='$1,000.00'
            assert app.variables['buys'].get()=='0 completed'
            if a.screenshot:
                try:
                    from PIL import ImageGrab
                    root.lift(); root.update(); time.sleep(.3)
                    x,y=root.winfo_rootx(),root.winfo_rooty()
                    ImageGrab.grab(bbox=(x,y,x+root.winfo_width(),y+root.winfo_height())).save(a.screenshot)
                except ImportError: print('Screenshot requires optional Pillow; GUI tests still passed',file=sys.stderr)
            root.destroy()
            print('Desktop GUI smoke test passed. No worker/account connection started.')
        else:
            root.mainloop()
    finally:
        if 'app' in locals() and app.power:app.power.close()
        lock.close()
    return 0


if __name__=='__main__': raise SystemExit(main())
