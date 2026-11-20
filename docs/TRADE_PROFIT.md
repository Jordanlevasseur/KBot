# Trade-history profit column

The desktop Trade history tab now shows PROFIT in dollars on each BUY fill.
This is realized net profit: confirmed sale proceeds or settlement payout minus
entry cost, entry fees and allocated exit fees. Multiple buy fills use FIFO cost
basis within the recorded order sequence; fractional fills retain Decimal precision.

- Open: no part of that BUY fill has closed yet.
- A dollar amount with (partial): profit realized so far; shares remain open.
- A plain dollar amount: the fill is completely sold or settled.
- SELL rows show a dash because their proceeds/fees are accounted for on the BUY
  rows, avoiding duplicate profit. Missing/incomplete cost information also shows
  a dash. Read-only account-monitor modes do not infer profit from partial histories.

Confirmed settlement revenue is used, including zero payouts. Settlement aggregate
fee totals are not subtracted again because each actual fill's fees are already
included. No unrealized prices or theoretical winnings are used. Display rounds
to cents; internal calculations use Decimal. The existing overall P/L card is unchanged.

Calculation runs in one coalescing desktop background reader, on short read-only
SQLite snapshots for the visible markets, including older entry fills when needed.
It does not modify journal records or schemas, access credentials, call an exchange,
change trade authorization, or add work to the order-execution path. Results refresh
automatically; stale results for another mode/view/generation are discarded.

Validation: 346 offline tests passed, zero failed (29.056s). Nine profit tests cover
YES/NO wins/losses, entry/exit fees, partial/fractional fills, two purchases, unknown
basis, actual held-side prices, reversed fill arrival, unchanged database contents,
and the simulation's existing net settlement result. The native dashboard test
verified the new column and automatic Open-to-settled profit updates using a
disposable simulated journal. Peak GUI test RSS was 44.50 MiB.

Only desktop.py and the new profits.py application module changed. Every other
installed Python module was verified byte-identical before/after installation.
The running trading session and gateway were not restarted. Reopen the desktop
window at a convenient stopping point to load the new column.
