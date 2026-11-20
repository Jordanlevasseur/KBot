"""Zero-dependency terminal interface with deterministic offline market replay."""
import argparse
import json
import select
import resource
import sys
import time
from pathlib import Path
from .core import Engine, Quote
from .security import check_isolation


def replay(t):
    cycle, offset = divmod(t, 900)
    # Final four-minute entry window: first buy, stop, rebound and second buy.
    bid, ask = (94, 96)
    if 800 <= offset < 810:
        bid, ask = 85, 87
    elif 810 <= offset < 820:
        bid, ask = 88, 90
    elif offset >= 820:
        bid, ask = 96, 97
    return Quote(f'KXBTC15M-SIM-{cycle}', (cycle+1)*900, t,
                 bid, 100-ask, ask, 100-bid, liquidity=200)


def feed(engine, t):
    # Synthetic settlement is an exchange event, including after an outage.
    overdue = engine.db.execute('SELECT ticker,cutoff FROM markets WHERE settled=0 AND cutoff<=?', (t,)).fetchall()
    for market in overdue:
        engine.tick(Quote(market['ticker'], market['cutoff'], t, None, None,
                          None, None, result='yes'))
    q = replay(t)
    engine.tick(q)
    return q


def display(engine, q, connected=True):
    s = engine.status()
    a = s['account']
    p = s['positions'][0] if s['positions'] else None
    m = engine.row('SELECT * FROM markets WHERE ticker=?', (q.ticker,))
    print('\033[2J\033[H', end='')
    print('KALSHI BITCOIN BOT | OFFLINE SIMULATION | No account connection')
    print('Replay feed:', 'connected' if connected else 'disconnected',
          '| Entries:', 'STARTED' if a['running'] else 'STOPPED')
    print(f'Market: {q.ticker} | Cutoff in: {max(0,q.cutoff-q.now)} virtual seconds')
    print(f'Cash: ${a["cash"]/100:.2f} | Realized P/L: ${a["pnl"]/100:.2f} | Buys: {m["buys"] if m else 0}')
    if p:
        print(f'Position: {p["qty"]} {p["side"].upper()} | Cost incl fees: {p["cost"]}c | Stop latched: {bool(p["stop"])}')
        print('Executable bid:', getattr(q, p['side']+'_bid'), 'cents')
        entries = engine.row("SELECT sum(f.qty*f.price) AS cost,sum(f.qty) AS n FROM fills f JOIN orders o ON o.id=f.order_id WHERE o.ticker=? AND o.action='buy' AND o.id=(SELECT max(id) FROM orders WHERE ticker=? AND action='buy' AND counted=1)", (q.ticker,q.ticker))
        if entries and entries['n']:
            print(f'Entry average: {entries["cost"]/entries["n"]:.2f}c')
    else:
        print('Position: none | Stop: inactive')
    print('Active orders:', json.dumps(s['active_orders']))
    print('Simulation only. Buy limits use the configured threshold through 99c; cheaper fills allowed. Use run-trading.sh for the separate trading candidate.')
    for e in reversed(s['recent_events'][:6]):
        print(f'{e["kind"]}: {e["detail"]}')
    print('\ns=start entries, p=stop entries, h=history, l=logs, d=disconnect, c=reconnect, q=quit')
    print('Enter a command then press Enter. Protective selling continues when entries stop.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default='state/simulation.sqlite3')
    parser.add_argument('--steps', type=int, help='Headless deterministic replay ticks, then exit')
    parser.add_argument('--interval', type=float, default=1.0, help='Wall seconds per replay step (minimum 0.05)')
    parser.add_argument('--speed', type=int, default=5, help='Virtual seconds per tick')
    parser.add_argument('--mode', choices=['simulation','demo','live'], default='simulation')
    parser.add_argument('--check-isolation', action='store_true', help='Verify sandbox and exit')
    parser.add_argument('--start', action='store_true', help='Start SIMULATED entries')
    args = parser.parse_args()
    if args.mode != 'simulation':
        parser.error('This command runs simulation only; use deploy/run-trading.sh for external execution. No connection attempted.')
    if args.speed < 1 or args.interval < .05 or (args.steps is not None and args.steps < 0):
        parser.error('Invalid replay timing')
    try:
        isolation = check_isolation()
    except RuntimeError as exc:
        parser.error(str(exc) + '; use deploy/run-sandboxed.sh')
    if args.check_isolation:
        print(json.dumps(isolation, indent=2))
        return
    resource.setrlimit(resource.RLIMIT_AS, (500*1024*1024, 500*1024*1024))
    engine = Engine(Path(args.db))
    if args.start:
        engine.start()
    # Resume deterministic replay after last persisted timestamp.
    saved = engine.row('SELECT max(last_tick) AS tick FROM markets')
    t = max(0, (saved['tick']+1) if saved['tick'] is not None else 775)
    connected = True
    try:
        if args.steps is not None:
            for _ in range(args.steps):
                # Feed every virtual second to avoid missing cutoff/settlement.
                feed(engine, t)
                t += 1
            engine.stop()
            print(json.dumps(engine.status(), indent=2))
            return
        if not sys.stdin.isatty():
            parser.error('Interactive mode requires a terminal; use --steps for headless replay')
        while True:
            q = replay(t)
            if connected:
                q = feed(engine, t)
            else:
                engine.tick(Quote(**{**q.__dict__, 'connected': False}))
            display(engine, q, connected)
            ready, _, _ = select.select([sys.stdin], [], [], args.interval)
            if ready:
                command = sys.stdin.readline().strip().lower()
                if command == 'q':
                    break
                if command == 's':
                    engine.start()
                elif command == 'p':
                    engine.stop()
                elif command == 'd':
                    connected = False
                elif command == 'c':
                    connected = True
                    # Never retrospectively trade on unseen quotes.
                elif command in ('h','l'):
                    table = 'fills' if command == 'h' else 'events'
                    print(json.dumps([dict(r) for r in engine.db.execute(f'SELECT * FROM {table} ORDER BY id DESC LIMIT 50')], indent=2))
                    input('Press Enter to return')
            for advance in range(1, args.speed):
                if connected:
                    feed(engine, t+advance)
            t += args.speed
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()
        engine.close()


if __name__ == '__main__':
    main()
