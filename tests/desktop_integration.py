"""Real Linux sandbox + GUI exercise. Synthetic data only; requires a display."""
import json
import os
from pathlib import Path
import queue
import sqlite3
import sys
import tempfile
import time
import tkinter as tk
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from kalshi_bot.desktop import Application
from kalshi_bot.desktop_runtime import Worker,sandbox_command

ROOT=Path(__file__).resolve().parent.parent
with tempfile.TemporaryDirectory() as temporary:
    temporary=Path(temporary)
    events=queue.Queue(); worker=Worker(events)
    state=temporary/'worker'; state.mkdir()
    command=sandbox_command(ROOT,state,'simulation')
    worker.launch(command)
    def receive(predicate,timeout=15):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            generation,message=events.get(timeout=max(.1,deadline-time.monotonic()))
            if generation!=worker.generation: continue
            if message.get('type')=='error': raise AssertionError(message['message'])
            if predicate(message): return message
        raise AssertionError('Worker state timed out')
    initial=receive(lambda m:m.get('type')=='state')['state']
    assert initial['running'] is False and initial['position'] is None
    worker.send('start')
    held=receive(lambda m:m.get('type')=='state' and bool(m['state']['position']))['state']
    worker.send('pause')
    paused=receive(lambda m:m.get('type')=='state' and not m['state']['running'])['state']
    assert paused['position'] and paused['history']
    first_quantity=paused['position']['qty']
    finished=[]; worker.stop(callback=lambda:finished.append(True))
    deadline=time.monotonic()+15
    while not finished and time.monotonic()<deadline: time.sleep(.05)
    assert finished
    worker.launch(command)
    restored=receive(lambda m:m.get('type')=='state')['state']
    assert restored['running'] is False and restored['position']['qty']==first_quantity
    finished=[]; worker.stop(callback=lambda:finished.append(True))
    deadline=time.monotonic()+15
    while not finished and time.monotonic()<deadline: time.sleep(.05)
    assert finished
    os.environ['XDG_STATE_HOME']=str(temporary/'ui-state')
    os.environ['XDG_CONFIG_HOME']=str(temporary/'ui-config')
    root=tk.Tk(); root.withdraw(); app=Application(root,autostart=False)
    profit_path=app.state_root/'simulation/desktop-simulation.sqlite3';profit_path.parent.mkdir(parents=True,exist_ok=True)
    original=sqlite3.connect((state/'desktop-simulation.sqlite3').as_uri()+'?mode=ro',uri=True);copied=sqlite3.connect(profit_path)
    try:original.backup(copied)
    finally:original.close();copied.close()
    app.update_state({**held,'buys':7}); root.update()
    assert app.variables['buys'].get()=='7 completed'
    app.update_state(held); root.update()
    deadline=time.monotonic()+2
    while time.monotonic()<deadline and not app.profit_values:root.update();time.sleep(.01)
    assert 'profit' in app.history['columns']
    assert any(app.history.item(row,'values')[-1]=='Open' for row in app.history.get_children())
    assert app.variables['position'].get().endswith('YES')
    assert app.variables['entry'].get()=='96.00¢'
    assert 'YES • BUY 96.00¢' in app.buy_quotes.get()
    assert app.history.get_children()
    old_rows=app.history.get_children()
    app.update_state(held)
    assert app.history.get_children()==old_rows  # No flicker/scroll reset on identical fills.
    # Settlement updates the existing BUY rows asynchronously, with no new trade.
    from kalshi_bot.core import Engine,Quote
    from kalshi_bot.desktop_worker import simulation_state
    profit_engine=Engine(profit_path)
    try:
        ticker=held['position']['ticker'];market=profit_engine.row('SELECT cutoff FROM markets WHERE ticker=?',(ticker,))
        terminal=Quote(ticker,market['cutoff'],market['cutoff'],99,0,100,1,result=held['position']['side'])
        profit_engine.tick(terminal);completed=simulation_state(profit_engine,terminal)
    finally:profit_engine.close()
    app.update_state(completed);deadline=time.monotonic()+2
    while time.monotonic()<deadline and not any(value.get('status')=='closed' for value in app.profit_values.values()):root.update();time.sleep(.01)
    assert any(app.history.item(row,'values')[-1].startswith('+$') for row in app.history.get_children())
    # A confirmed losing settlement must show a signed red loss, not an absolute gain.
    profit_engine=Engine(profit_path)
    try:
        profit_engine.start();profit_engine.tick(Quote('KXBTC15M-LOSS',1800,1680,97,2,98,3))
        losing=Quote('KXBTC15M-LOSS',1800,1800,0,99,1,100,result='no')
        profit_engine.tick(losing);loss_state=simulation_state(profit_engine,losing)
    finally:profit_engine.close()
    app.update_state(loss_state);deadline=time.monotonic()+2
    while time.monotonic()<deadline and not any(app.history.item(row,'values')[-1].startswith('-$') for row in app.history.get_children()):root.update();time.sleep(.01)
    negatives=[row for row in app.history.get_children() if app.history.item(row,'values')[-1].startswith('-$')]
    assert negatives and all('loss' in app.history.item(row,'tags') for row in negatives)
    assert app.history.heading('profit','text')=='PROFIT / LOSS'
    # Existing live BUY/SELL rows gain exact timestamps from the background reader.
    from kalshi_bot.profits import trade_time_text
    time_path=app.state_root/'production-trading/desktop-trading.sqlite3';time_path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(time_path)
    db.executescript('CREATE TABLE orders(exchange_id TEXT,ticker TEXT,side TEXT,action TEXT);CREATE TABLE fills(id TEXT,order_id TEXT,payload TEXT);CREATE TABLE settlements(ticker TEXT,payload TEXT);')
    clock_history=[];expected_times={}
    for identity,action,price,stamp in (('clock-buy','buy','0.98','2026-11-14T10:29:45.329642Z'),('clock-sell','sell','0.86','2026-11-14T10:29:51.326398Z')):
        fill={'fill_id':identity,'count_fp':'3','yes_price_dollars':price,'no_price_dollars':str(1-float(price)),'fee_cost':'.01','created_time':stamp}
        db.execute('INSERT INTO orders VALUES(?,?,?,?)',(identity,'KXBTC15M-CLOCK','yes',action))
        db.execute('INSERT INTO fills VALUES(?,?,?)',(identity,identity,json.dumps(fill)))
        clock_history.append({'id':identity,'ticker':'KXBTC15M-CLOCK','action':action,'side':'yes','qty':'3','price':str(float(price)*100),'fee':'1'})
        expected_times[identity]=trade_time_text(fill)
    db.commit();db.close()
    app.update_state({**held,'mode':'production-trading','position':None,'history':clock_history})
    deadline=time.monotonic()+2
    while time.monotonic()<deadline and not app.trade_times:root.update();time.sleep(.01)
    assert app.history.heading('time','text')=='TIME (LOCAL)'
    shown={app.history.item(row,'values')[1]:app.history.item(row,'values')[0] for row in app.history.get_children()}
    assert shown==expected_times
    # Raw read-only API fills also render their own timestamps without a journal lookup.
    raw=clock_history[0].copy();raw['created_time']='2026-11-14T10:29:46Z'
    app.update_state({**held,'mode':'production-monitor','position':None,'history':[raw]})
    assert app.history.item(app.history.get_children()[0],'values')[0]==trade_time_text(raw)
    live={**held,'mode':'production-trading','connection':'Production account connected',
          'running':True,'market':'KXBTC15M-OLD','remaining':1,'clock_rate':1,'clock_sample_mono':time.monotonic()}
    app.update_state({**live,'cash_scope':'market','cash_cents':620})
    assert app.cash_label.get()=='AVAILABLE FOR BITCOIN'
    assert not hasattr(app,'prepared_mode')  # Preparation is standard, with no opt-in control.
    app.update_state({**live,'prepared_entry':{'ready':True,'reason':'Exact order quantities cached','reserved_cents':303}})
    assert 'Prepared orders: READY' in app.prepared_status.get() and '$3.03' in app.prepared_status.get()
    app.update_state(live)
    assert app.cash_label.get()=='TOTAL ACCOUNT CASH'
    assert app.session_badge.cget('text')=='LIVE TRADING • ON'
    live.update(market='KXBTC15M-NEW',remaining=900,running=False,clock_sample_mono=time.monotonic())
    app.update_state(live)
    assert app.variables['market'].get()=='KXBTC15M-NEW'
    assert app.variables['time'].get()=='15:00'
    assert app.session_badge.cget('text')=='LIVE TRADING • PAUSED'
    live.update(yes_ask_cents='2',no_ask_cents='99',events=[{'kind':'audit','detail':'Decision: '+json.dumps({'ticker':'KXBTC15M-NEW','status':'No qualifying entry','yes_ask':'.02','no_ask':'.99'})}])
    app.update_state(live)
    assert 'NO • BUY 99.00¢' in app.buy_quotes.get()
    details=app.activity.item(app.activity.get_children()[0],'values')[1]
    assert 'NO buy 99.00¢' in details and 'Decision: {' not in details
    with patch.object(app.worker,'send',return_value=True) as send:
        app.refresh()
        send.assert_called_once_with('refresh')
        assert app.data['running'] is False
    app.last_state_at-=16
    app.render_clock()
    assert 'STALE' in app.session_badge.cget('text')
    # A small YES figure must never appear as the held NO sell price.
    sided={**initial,'mode':'production-trading','market':'KXBTC15M-NO',
           'position':{'ticker':'KXBTC15M-NO','side':'no','qty':'3'},
           'yes_bid_cents':'1.8','yes_ask_cents':'2','no_bid_cents':'98','no_ask_cents':'98.2',
           'bid_cents':'1.8'}  # Ignore a contradictory legacy generic field.
    app.update_state(sided)
    assert app.variables['bid'].get()=='NO 98.00¢'
    assert 'YES • BUY 2.00¢ / SELL 1.80¢' in app.buy_quotes.get()
    assert 'NO • BUY 98.20¢ / SELL 98.00¢' in app.buy_quotes.get()
    app.update_state({**sided,'position':None})
    assert app.variables['bid'].get()=='No position'
    app.update_state({**sided,'position':{**sided['position'],'ticker':'KXBTC15M-OLD'}})
    assert app.variables['bid'].get()=='—'
    # An independent market event keeps flowing while the account view still
    # holds an old-market position or has not yet completed another REST cycle.
    app.update_state(sided)
    market_event={'market':'KXBTC15M-NEXT','remaining':850,'clock_sample_mono':time.monotonic(),
                  'yes_bid_cents':'59','yes_ask_cents':'60','no_bid_cents':'40','no_ask_cents':'41',
                  'market_health':'connected','market_last_update':time.time(),
                  'position_ticker':'KXBTC15M-NO','position_bid_side':'no','position_bid_cents':'99'}
    app.update_market(market_event)
    app.update_state(sided)  # Late account update must not undo the rollover.
    assert app.variables['market'].get()=='KXBTC15M-NEXT'
    assert app.variables['bid'].get()=='NO 99.00¢'
    assert app.variables['buys'].get()=='0 completed'
    assert 'NO • BUY 41.00¢' in app.buy_quotes.get()
    assert 'last price change' in app.market_stamp.get()
    root.after(20,lambda:app.update_market({**market_event,'no_ask_cents':'42'}))
    deadline=time.monotonic()+.1
    while time.monotonic()<deadline:root.update();time.sleep(.01)
    assert 'NO • BUY 42.00¢' in app.buy_quotes.get()  # Works while withdrawn.
    # Connection status is independent of prepared-account cache readiness.
    verified_market={**market_event,'market_verified_age':.1,'clock_sample_mono':time.monotonic()}
    api_healthy={**live,'running':True,'account_connected':True,'connection_phase':'connected','auto_reconnecting':False,
                 'prepared_entry':{'ready':False,'reason':'Refreshing account checks','reserved_cents':0}}
    app.update_market(verified_market);app.update_state(api_healthy);app.render_clock()
    assert app.connect_button.cget('text')=='Connected ✓'
    assert str(app.connect_button.cget('state'))=='disabled'
    assert app.session_badge.cget('text')=='LIVE TRADING • ON'
    app.update_state({**api_healthy,'account_connected':False,'connection_phase':'reconnecting','auto_reconnecting':True});app.render_clock()
    assert 'Retrying automatically' in app.connect_button.cget('text')
    assert str(app.connect_button.cget('state'))=='disabled'
    assert 'RECONNECTING' in app.session_badge.cget('text')
    assert app.data['running'] is True
    app.last_state_at=None;app.market_data={};app.render_clock()
    assert 'DISCONNECTED' in app.session_badge.cget('text')  # A stopped worker cannot appear active/retrying.
    app.update_market(verified_market)
    app.update_state(api_healthy);app.update_market({**verified_market,'market_health':'saving stop'});app.render_clock()
    assert 'SAVING PROTECTION' in app.session_badge.cget('text')
    app.update_market({**verified_market,'market_verified_age':6});app.render_clock()
    assert 'STALE' in app.session_badge.cget('text')  # Never hide genuinely expired data.
    app.update_market(market_event);app.update_state(sided)
    app.update_market({**market_event,'no_ask_cents':'98','eligible_sides':['no'],'entry_window_open':True,
                       'execution_stage':'Reconciling account','entries_enabled':True,'execution_at':100})
    assert 'NO ask 98.00¢' in app.eligibility_status.get()
    assert 'Reconciling account' in app.eligibility_status.get()
    app.update_market({**market_event,'no_ask_cents':'98.4','eligible_sides':['no'],'entry_window_open':True,
                       'eligible_limits_cents':{'no':'99'},'execution_stage':'Checking balance and fees','entries_enabled':True,'execution_at':100})
    assert 'NO ask 98.40¢ → limit 99.00¢' in app.eligibility_status.get()
    assert 'Checking balance and fees' in app.eligibility_status.get()
    app.update_market({**market_event,'remaining':30,'entry_window_open':False,'execution_stage':'New purchases blocked during final 30 seconds'})
    assert 'new purchases blocked: final 30 seconds' in app.eligibility_status.get()
    app.update_market({**market_event,'strategy_decision':{'decision_count':12,'actual_interval_ms':250.5,
                       'reason':'NO qualifies; awaiting fresh account reconciliation'}})
    assert 'Strategy check #12' in app.strategy_status.get()
    assert '250 ms' in app.strategy_status.get() or '251 ms' in app.strategy_status.get()
    assert 'awaiting fresh account reconciliation' in app.strategy_status.get()
    app.update_market({**market_event,'strategy_decision':{'decision_count':12,'actual_interval_ms':250,
                       'decision_mono':time.monotonic()-1,'reason':'Awaiting fresh account reconciliation'}})
    assert 'DELAYED' in app.strategy_status.get()
    assert 'enabled' in app.selection_status.get()
    app.update_market({**market_event,'entries_enabled':False,'execution_at':101,'execution_stage':'Entries paused'})
    app.update_state({**sided,'running':True,'execution_at':99})
    assert app.data['running'] is False  # Late reply cannot undo the acknowledged pause.
    app.update_state({**sided,'execution_at':102,'running':False,
                      'events':[{'kind':'audit','at':time.time(),'detail':'Decision failure: '+json.dumps({
                          'stage':'Submitting buy','outcome':'rejected','reason':'Kalshi request failed (HTTP 403)'})}]})
    assert 'HTTP 403' in app.activity.item(app.activity.get_children()[0],'values')[1]
    app.data={}; app.update_state(initial)
    app.mode.set('Demo account • read only')
    with patch('kalshi_bot.desktop.messagebox.showinfo'):
        app.connect()  # Missing keys do not create a worker or connection.
    assert app.worker.process is None
    root.destroy()
    print(json.dumps({'result':'passed','checks':['real namespace isolation','start/pause IPC','fills/history','persistent restart paused','GUI real-fill rendering','missing-credential refusal','market rollover display','live status badge','refresh preserves pause','stale-data indicator','stable history rows','strategy check cadence','delayed strategy indicator','account revalidation keeps connection','automatic reconnect indicator','protection-save indicator','actual stream expiry remains stale','uncapped purchase counter','final 30-second purchase cutoff display','local confirmed BUY/SELL fill times','historical timestamp backfill','read-only fill times','profit column','async open-to-settled net profit','negative realized loss and red loss styling'],
                      'gui_test_peak_rss_mib':round(int(next(x for x in Path('/proc/self/status').read_text().splitlines() if x.startswith('VmHWM:')).split()[1])/1024,2),
                      'current_gui_rss_mib':round(int(next(x for x in Path('/proc/self/status').read_text().splitlines() if x.startswith('VmRSS:')).split()[1])/1024,2)},indent=2))
