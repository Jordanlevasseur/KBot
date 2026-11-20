"""Safe public error diagnostics: fixed known codes, never server free text.

Reference: https://docs.kalshi.com/getting_started/order_errors
"""
import json

REASONS={
    'invalid_order':'Order fields or execution settings were rejected',
    'invalid_parameters':'An order parameter was rejected',
    'invalid_price':'The price is not on the market price grid',
    'invalid_order_size':'The contract quantity is not supported by this market',
    'insufficient_balance':'Insufficient available cash on the market exchange shard',
    'insufficient_shard_balance':'The requested exchange shard has no funded balance',
    'too_many_open_orders':'The exchange open-order limit was reached',
    'exceeded_order_group_contracts_limit':'The exchange order-group limit was reached',
    'invalid_amend_quantity_for_order':'The requested quantity change was rejected',
    'order_ticker_mismatch':'The order market does not match',
    'order_side_mismatch':'The order side does not match',
    'order_id_and_client_order_id_mismatch':'Order identifiers do not match',
    'AMEND_ORDER_NO_OP':'The requested order change has no effect',
    'EXPIRED_TIMESTAMP':'The exchange rejected an expired order timestamp',
    'Cannot_specify_both_IoC_and_expiration_timestamp':'The expiry conflicts with the execution setting',
    "Post_only_but_execution_type_can't_rest":'The post-only execution settings conflict',
    'too_many_orders_in_batch':'The batch order limit was exceeded',
    'not_found':'The requested order was not found on this shard',
    'market_not_found':'The market was not found on the requested shard',
    'order_group_not_found':'The order group was not found on the requested shard',
    'order_already_exists':'This client order ID is in use; reconcile the existing order',
    'fill_or_kill_insufficient_resting_volume':'There was insufficient volume for the execution setting',
    'market_inactive':'The exchange says this market is not active',
    'trading_is_paused':'The exchange has paused new orders',
    'exchange_is_paused':'The exchange is paused',
    'TRADING_BLOCKED':'The account or subaccount is not permitted to place this order',
    'permission_denied':'The API key is not permitted to use this endpoint',
    'subaccount_is_restricted_by_API_key':'The requested subaccount is outside this key permissions',
    'deprecated_v1_order_endpoint':'The requested order endpoint is deprecated',
    'too_many_requests':'The exchange rate limit was reached',
    'service_unavailable':'The exchange service is unavailable; order outcome may be unknown',
    'internal_server_error':'The exchange had an internal error; order outcome may be unknown',
    'context_canceled':'The request was interrupted; order outcome may be unknown',
    'deadline_exceeded':'The request timed out; order outcome may be unknown',
    'context_deadline_exceeded':'The request timed out; order outcome may be unknown',
}


def public_code(raw):
    if not isinstance(raw,(str,bytes)) or len(raw)>16384:return None
    try:value=json.loads(raw)
    except (ValueError,UnicodeError,RecursionError):return None
    if not isinstance(value,dict):return None
    error=value.get('error',value)
    if not isinstance(error,dict):return None
    code=error.get('code')
    return code if isinstance(code,str) and code in REASONS else None
