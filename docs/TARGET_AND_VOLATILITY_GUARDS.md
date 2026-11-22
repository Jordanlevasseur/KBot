# Target distance and volatility authorization

New automatic entries require a fresh official BRTI value and the verified
market's `floor_strike`. YES requires BRTI minus target >= $50. NO requires
target minus BRTI >= $50. At exactly $50 entry is allowed; wrong-side prices
never qualify simply because their absolute distance is large.

A held position latches protection when that same signed favorable cushion
reaches $35 or less. Crosses through the target also latch. This supplements
the configured contract bid trigger. All remaining contracts use the marketable
protective execution described in MARKETABLE_PROTECTION.md. No bid liquidity
means no execution guarantee. A missing reference blocks buys; it does not
disable existing contract-price protection or an already latched exit.

Entry authorization is checked when selecting a prepared plan and again in the
before-wire hook. Neither check makes a network request. Reference age is bounded
at 2.5 seconds on both the source wall clock and monotonic receipt clock. Target
and market tick-grid changes invalidate prepared state.

Volatility requires a valid latest sample no older than 2.5 seconds and at least
45 distinct sampled seconds spanning 55 seconds in the past minute. Missing or
insufficient data is a block, not zero volatility. The one-minute shock filter
uses actual high-low range; five- and fifteen-minute limits use the averages of
rolling 30-second ranges. Equality blocks. Existing configured limit values are
retained. The desktop's market panel consumes the trading runtime's volatility
snapshot instead of displaying independently sampled averages.

Order-intent evidence includes the entry authorization's target, BRTI timestamp,
signed distance, volatility and limits. Order acknowledgments and recorded fills
include the current reference when available, marked unavailable otherwise.
Fill annotation is a reconciliation-time snapshot, NOT necessarily the exchange
fill-time BRTI; use its timestamp/age and fetch historical ticks for exact analysis.

Only the prepared demo/live runtime gains reference-based entry/exit guards.
The synthetic simulator has no authentic Bitcoin reference feed and must not be
used to claim this underlying-price protection is verified against a real exchange.

Deployment requires coordinated application and gateway validator updates while
closed and flat. Keep live entries paused. Do not install just the application
and leave the old gateway rejecting the changed protective execution envelope.
