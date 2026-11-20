"""Read-only protective-exit diagnostics from confirmed journal state.

A create acknowledgment is not a fill. Settlement is not a protective sale.
"""
from decimal import Decimal as D


def exit_state(journal,ticker=None):
    params=(ticker,) if ticker else ()
    buy=journal.db.execute("SELECT rowid AS ordinal,* FROM orders WHERE action='buy'"+
                           (' AND ticker=?' if ticker else '')+' ORDER BY rowid DESC LIMIT 1',params).fetchone()
    empty={'state':'not_triggered','message':'Not triggered','triggered':False,'completed':False}
    if not buy:return empty
    ticker,side=buy['ticker'],buy['side']
    market=journal.db.execute('SELECT stop,settled FROM markets WHERE ticker=?',(ticker,)).fetchone()
    sells=journal.db.execute("SELECT * FROM orders WHERE ticker=? AND side=? AND action='sell' AND rowid>? ORDER BY rowid",(ticker,side,buy['ordinal'])).fetchall()
    bought=D(buy['filled']);sold=sum((D(order['filled']) for order in sells),D(0));remaining=bought-sold
    last=sells[-1] if sells else None
    triggered=bool(market and market['stop'])
    settled=bool(market and market['settled'])
    state,message='not_triggered','Not triggered'
    if triggered:
        if bought==0 and buy['status'] not in ('pending','resting'):
            state,message='no_position','No filled position • no protective sale'
        elif bought>0 and sold==bought:
            state,message='completed','Exit completed • confirmed sell fills'
        elif settled:
            state,message='settled_without_full_exit','Settled • protective exit was not fully filled'
        elif buy['status'] in ('pending','resting'):
            state,message='waiting_for_buy_cancel','LATCHED • waiting for residual buy cancellation'
        elif last is None:
            state,message='triggered','LATCHED • sell not submitted'
        elif last['status']=='rejected':
            state,message='rejected','LATCHED • sell rejected; retry required'
        elif last['status'] in ('canceled','expired','executed'):
            state,message='retry_required','LATCHED • remaining quantity needs an exit'
        elif sold>0:
            state,message='partially_filled','LATCHED • partial sell fill; remaining order open'
        elif last['status']=='pending' and not last['exchange_id']:
            state,message='submission_uncertain','LATCHED • sell submission uncertain; reconcile'
        elif last['status']=='pending':
            state,message='acknowledged','LATCHED • sell acknowledged; awaiting reconciliation'
        else:
            state,message='accepted','LATCHED • sell accepted/open; unfilled'
    return {'state':state,'message':message,'triggered':triggered,'completed':state=='completed',
            'side':side,'bought_qty':str(bought),'sold_qty':str(sold),'unsold_qty':str(remaining),
            'order_status':last['status'] if last else None,'order_acknowledged':bool(last and last['exchange_id']),
            'settled':settled}
