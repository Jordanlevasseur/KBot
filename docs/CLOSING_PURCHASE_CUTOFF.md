# No purchases in the final 30 seconds

New purchases require more than 30 and no more than 240 seconds remaining until the official market trading cutoff. Exactly 30 seconds remaining is blocked. The rule applies to both YES and NO, including every repeat purchase. Completed purchase counts remain uncapped, with full sale and reconciliation required before each re-entry.

New limit buys expire at official close minus 30 seconds, so an earlier resting order cannot keep filling inside the blocked period. A final guard checks the purchase deadline after recording the durable intent and again before transmission. Residual buys are canceled/reconciled at the deadline without duplicate cancellation requests. The simulator also refuses pending entry fills after the deadline and retains the earliest recorded official close.

Protective stops remain active through the official trading cutoff. Sell limits remain 86 cents and expire at market close. The blackout does not force a sale or change settlement behavior, sizing, fees, or permitted buy prices. No order endpoints, credential storage, firewall, account scope, or gateway routing changes were made. Existing gateway validation accepts the earlier absolute buy expiry; no gateway service update is needed.

Validation: 370 offline tests passed with zero failures. Coverage includes 240/31/30.001/30/29.999/15/1/0-second boundaries, both sides, final transmission crossing, early buy expiration, partial-entry cancellation, no duplicate writes, earliest-cutoff preservation, and protective selling with 10 and 1 seconds remaining. Native dashboard and isolated mocked demo checks also passed. No real-money orders, account requests, production journal writes, or worker/service restarts were made for this update.

The installed update takes effect at the application's next normal launch. Existing exchange orders are not modified by the installer. See closing-purchase-cutoff-verification.json for integration results.
