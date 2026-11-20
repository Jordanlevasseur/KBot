> This guide describes the read-only deployment. A separate, explicitly enabled
> trading candidate is now available; see [TRADING.md](TRADING.md). The user approved
> cheaper fills, so the former strict fill-floor statements below are superseded.

# Read-only Kalshi account connection

The account connector is implemented and tested against generated keys and mocked
API responses. **It has not connected to an actual Kalshi account or demo account.**
Administrator installation and your local credentials are still required. Completing
these steps enables account inspection, not automated trading. Buy limits in the separate trading candidate remain 95c–99c; cheaper fills are allowed. No buy, sell, cancellation, deposit, withdrawal, transfer,
or API-key-management request can pass this connector or gateway.

## 1. Prerequisites

Use your distribution's approved packages: `python3`, `python3-cryptography`,
`bubblewrap`, `nftables`, `acl` (`setfacl`), CA certificates, and systemd with
`SocketBindDeny` support. No packages or updates are downloaded by the application.
Unsupported isolation causes startup to fail; do not disable these checks.

Start with a dedicated **demo** key, separate from production credentials. Select
read-only permissions for balances, positions, orders, fills, and settlements if
your account/key-creation interface exposes those scopes. Do not grant trading or
fund-transfer permissions. API-key types/scopes vary; verify the permissions shown
by Kalshi when creating the key. Ed25519 and RSA >=2048-bit keys are supported.

Do not paste a private key into chat. Keep credentials outside this repository and
outside the writable simulation/account state directories.

## 2. Configure credentials locally — no connection

From the project directory, create the parent configuration directory if needed,
then run the local setup command. The destination must be a new directory:

```sh
mkdir -p "$HOME/.config"
python3 -m kalshi_bot.credentials --environment demo --directory "$HOME/.config/kalshi-bot-demo"
```

The command privately prompts for your API key ID and asks for the path of your
already-downloaded PEM file. Set that source file to owner-only mode (`chmod 600`)
first. No private key text is entered on the command line. Setup validates the key
locally and creates a directory with mode 0700, holding `private.pem` and `config.json`
with mode 0600. Existing credential directories are never overwritten.

Validate without accessing Kalshi or even the gateway:

```sh
./deploy/run-account.sh "$HOME/.config/kalshi-bot-demo" demo
```

No arguments after the environment means **offline preflight**, never connect.
This verifies sandbox isolation, credential permissions, environment, and key format.
The full home directory is not mounted: the monitor sees only this one credential
directory, its account cache, its package, and (if installed) the gateway directory.

Production uses a different key and directory, configured with `--environment production`.
Demo credentials cannot be loaded for a production invocation or vice versa. Cached
snapshots are also bound to the key's public fingerprint and environment to prevent
showing one account's cache under a different key. Key rotation requires a new
account cache directory; the old cache is retained, never overwritten automatically.

## 3. Administrator installation — review before running

These commands are supplied for administrator review, **not executed by this build**.
They install root-owned code, a dedicated non-login gateway user, narrow nft rules,
and disabled systemd units. They do not start the gateway or authenticate to Kalshi.
Provisioning resolves the official host through the administrator's DNS resolver;
the runtime gateway has no DNS egress. Review local changes before using sudo:

```sh
sudo install -d -m 0755 /opt/kalshi-bot/kalshi_bot /opt/kalshi-bot/deploy
sudo install -m 0644 kalshi_bot/*.py /opt/kalshi-bot/kalshi_bot/
sudo install -m 0755 deploy/provision-gateway.py deploy/run-account.sh /opt/kalshi-bot/deploy/
sudo /usr/bin/python3 /opt/kalshi-bot/deploy/provision-gateway.py --environment demo --client-user "$USER"
```

The installation directory and all ancestors/files must be root-owned and not
writable by other users. Do not copy `state`, credentials, a virtual environment,
or unrelated files into `/opt/kalshi-bot`. Provisioning refuses an unexpected
reserved service user or untrusted deployment files.

Provisioning creates:

- Dedicated non-login user `kalshi-demo` (or `kalshi-production`).
- Group `kalshi-access`; it does not change your existing group memberships.
- Root-owned `/etc/kalshi-bot/demo-gateway.json` with official hostname, pinned
  resolved IP addresses, dedicated gateway UID, and your local client UID. No keys.
- An nft table `inet kalshi_demo` matching **only that dedicated gateway UID**.
  It permits TCP 443 to the pinned addresses and rejects all other IP egress.
  It does not flush the host firewall or alter other users' outbound rules.
- `kalshi-demo-policy.service`, which atomically restores that one table at boot/start.
- `kalshi-demo-gateway.service`, a disabled service that requires policy restoration
  and verifies exact kernel rules before launching.

The gateway runs with no privileges, a read-only host filesystem, hidden home
folders, bounded CPU/memory, no inherited credentials, and OS denial of Internet
socket binds. It checks bind denial before accepting any request. It listens only
on `/run/kalshi-demo/gateway.sock`. Named-user filesystem ACLs and kernel peer-UID
checks restrict that socket to the designated local client. No TCP listener exists.

No gateway service is started or enabled by provisioning. Firewall rules affect
only the dedicated service UID, which must not be used for any unrelated process.
Do not run the monitor or gateway as root.

## 4. First connection — only after explicit approval

Prefer the demo account first. Starting the gateway does not itself authenticate
or fetch API data; it waits for a request. The account monitor requires `--connect`
to authorize read-only requests for that invocation:

```sh
sudo systemctl start kalshi-demo-gateway.service
KALSHI_ACCOUNT_STATE="$HOME/.local/state/kalshi-bot-demo" /opt/kalshi-bot/deploy/run-account.sh "$HOME/.config/kalshi-bot-demo" demo --connect --once
```

Omit `--once` for the interactive monitor. It refreshes at most once every 30 seconds
by default, serializes requests, handles 429/5xx/network failures with bounded backoff,
and paginates collections. Controls: `r` refresh, `p` pause polling, `s` resume,
`h` confirmed fills, `o` open orders, `t` settlements, `l` logs, `q` quit.
`p` and `s` affect polling only; no trading control exists in account mode.

To connect a production account later, repeat the installation/configuration for
`production`, create a separate production key/cache, and use the production
service/directory names. **No production connection was authorized or attempted
in this build.** There is no flag that enables live order writes.

## What the monitor reports

Official balance in cents; nonzero signed YES/NO positions; resting account orders;
confirmed fills; settlements still exposed by the live endpoint; nearest open
`KXBTC15M` binary market and official `close_time`; fixed-point executable bids and
complementary asks derived from the official order book. Decimals are never rounded
into a falsely eligible whole-cent price.

API collections are read sequentially, not as an atomic account snapshot. A snapshot
is saved only after every required read succeeds. Failed reads retain a visibly
stale cache. Cache storage is private, separate from simulation, and limited to the
latest 100 snapshots and 1,000 log entries. Logs omit headers, key material, and
HTTP bodies. Unknown/malformed fields fail the refresh instead of inventing balances.

Existing personal positions are observed only. This monitor **does not protect them
with stop-loss orders**, cancel your orders, or infer a bot buy count. Inspect the
fills view for actual entry executions. Positions returned by the endpoint do not
include all historical account P/L; no fabricated account-wide total is shown.
Archived fills/settlements require historical APIs and are not included here.

## Network guarantees and operational limits

The monitor has a private network namespace and no Internet routes. Its only API
transport is a mounted Unix socket. The separate gateway accepts a small list of
GET endpoints, strips all non-authentication caller headers, rejects request bodies,
never follows redirects, pins destination IPs, and verifies TLS against the fixed
official hostname. The gateway does not receive the private key. It cannot submit
orders even if given a trading-capable API key.

The nft filter provides OS destination/port enforcement; the gateway's fixed-host
TLS check disambiguates shared CDN IPs. This combined boundary is necessary because
an IP firewall alone cannot distinguish hostnames sharing an address. Root/admin
control of the machine is trusted. Removing runtime firewall rules as root can
undermine the service; do not change them while the gateway runs.

DNS/IP changes can make the pinned gateway unavailable. Fail closed, stop it,
review DNS results, and rerun provisioning to update addresses. It does not discover
new destinations or download updates on its own. Existing host firewall restrictions
may block allowed traffic; this application does not override them. If kernel nft
or bind-denial checks fail, no gateway listener is started.

Reboot/start restores the saved IP policy before the gateway; it does not refresh
DNS automatically. Enabling boot startup is an explicit later admin choice:
`sudo systemctl enable kalshi-demo-gateway.service`. The monitor itself remains a
foreground program; there is no automatically enabled background account polling.

To stop: quit the monitor and `sudo systemctl stop kalshi-demo-gateway.service`.
The dedicated egress rules are deliberately retained when the service stops.
To remove a deployment, first disable/stop its gateway and policy services, then
have an administrator remove only the corresponding units, dedicated nft table,
configuration, and unused service account. Never flush unrelated firewall rules.
Revoke the API key in Kalshi to remove account access.

## Validation boundary

Offline tests verify cryptographic signatures, query signing, pagination, throttling,
retry limits, decimal prices, cache recovery/identity, credential modes, method/path
rejection, fake upstream gateway round trips, and firewall verification. A disposable
namespace test installed the actual nft policy and recorded a rejected connection.
Generated-key sandbox preflight passed. The privileged systemd installation, named
client ACL behavior under that deployed service, and actual Kalshi account responses
have not been exercised. A first demo read-only connection remains an acceptance
check, not a completed integration test or guarantee of production readiness.
