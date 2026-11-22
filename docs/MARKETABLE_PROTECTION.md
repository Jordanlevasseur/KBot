# Protective trigger and execution

The configured sell cents value is now a trigger, not the minimum acceptable
sale price. At or below it, the stop latches and the bot offers the full remaining
held quantity with a marketable good-till-canceled limit. That limit is the lowest
positive valid held-side price from the verified market's `price_ranges`. For NO,
this uses the complementary highest valid YES price. It can therefore consume
available bids below the trigger, continuing across price levels as liquidity
allows. The exchange can fill at prices better than the submitted limit.

No buyers still means no fill. Partial fills leave the remainder open. The
existing exclusive execution owner, position checks, reservations, reconciliation
and persistent stop latch remain mandatory. Missing/invalid grid metadata blocks
submission instead of inventing a price. Simulation has a whole-cent grid and
uses a 1-cent crossing sell limit.

The local strategy envelope marks protective exits so fractional remaining sell
quantities are not confused with complementary buys. The gateway strips this
envelope before sending to Kalshi. Both application and gateway validation code
must be deployed together, with the desktop stopped and no exposure/open orders.
Do not hot-update only one component or restart a gateway protecting an active
position. This behavior is a strategy change and may realize losses much larger
than the trigger gap.

Historical fixed-limit sell orders and fills remain unchanged. New behavior
applies to newly prepared exits only. This is not a claim of live fill verification.
