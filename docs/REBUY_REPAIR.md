# Rebuy refresh repair — November 21, 2026

The recent journal contains a second NO buy in `KXBTC15M-26NOV210915-15`
that ultimately canceled with zero fills, along with a cancellation/order
operation reporting HTTP 404. Other eligible quotes waited on account freshness.
A 404 alone does not establish cancellation, nonacceptance, or the underlying
exchange cause. Earlier conversational claims that volatility had definitely
passed were too strong: the dashboard candidate is not full execution approval.

Four independently refreshed account collections previously shared three worker
slots with cash, market metadata, trading status and fee reads. Paginated history
could delay the other prerequisites. Account collections now have four dedicated
slots, cash one, and metadata two. The existing rate gate, TTLs, generation guards,
cohort reconciliation and single execution owner are unchanged.

A cancellation 404 now records an explicit diagnostic and leaves the pending
order and its reservation in place, awaiting the next authoritative account
cohort. It does not escape into the generic execution-error path. No 404 is
treated as proof of cancellation and no duplicate order is authorized.

Regression coverage exercises blocked account reads alongside independent cash
and metadata, and a cancellation 404 across repeated execution ticks without
duplicate orders or repeated cancellation. This repair is source-only until a
controlled restart installs it; no production state was edited during testing.
