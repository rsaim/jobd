# The public demo

Everything that runs the hosted demo, kept in the repo: one free VM,
docker compose, Caddy in front, a nightly reset from cron.

The demo is uniquely cheap to host because it is synthetic end to end:
`jobd demo` replays gold labels through the real pipeline, so the instance
needs no model key, spends nothing per visitor, and contains nobody's real
mail. The only state visitors can touch (review queue, saved prompts) is
wiped by the reset.

## The pieces

| File | Role |
|---|---|
| `Caddyfile` | TLS, and a cookie gate: without the cookie every path serves the landing page; `GET /login?u=demo&p=demo` sets the cookie and redirects into the app. No browser auth popup anywhere. |
| `docker-compose.demo.yml` | Overlay on the root compose file: adds Caddy, unpublishes every other port, keeps cert storage in external volumes the reset cannot delete. |
| `landing/index.html` | The public landing page: a login form prefilled with the demo credentials, styled with the dashboard's own design tokens. |
| `oracle-provision.sh` | Creates the whole Oracle side from your laptop — VCN, firewall, instance with capacity retry — and lets cloud-init run `bootstrap.sh` on first boot. |
| `bootstrap.sh` | One-time VM setup: Docker, the OS firewall, the hostname, first build and seed. |
| `demo-restart.sh` | The reset: `down -v`, up, migrate, reseed. `--build` makes it a deploy. Refuses to run on any host `bootstrap.sh` has not stamped with `/etc/jobd-demo-host` — on a dev machine `down -v` would wipe the real database, raw store, and Gmail tokens. |
| `cron` | The nightly schedule (04:10 UTC), installed with `crontab demo/cron`. |

The gate is a speed bump for crawlers and scanners, not a secret — the
credentials are public, prefilled in the form, and checked by Caddy
itself, which answers a correct `/login` with a long-lived cookie. One
click logs a visitor in.

## Runbook A: from the CLI (recommended)

Only the account signup is manual — a card is required for identity
verification and is never charged for Always Free resources. After that:

```bash
brew install oci-cli                  # or: pip install oci-cli
oci session authenticate              # browser login; pick your home region
export OCI_CLI_AUTH=security_token
demo/oracle-provision.sh
```

The script creates the network, opens 22/80/443, launches the 2 OCPU /
12 GB A1 instance — retrying across availability domains when the region
is out of free ARM capacity, the free tier's one real obstacle — and
cloud-init runs `bootstrap.sh` on first boot. It exits by printing the
live URL once `https://<ip>.sslip.io` answers. Re-running is safe:
everything is looked up by name before being created.

## Runbook B: from the console

1. **Account and instance.** In an Oracle Cloud always-free account, create
   a compute instance: shape **VM.Standard.A1.Flex** at 2 OCPU / 12 GB —
   plenty for the demo, and the entire always-free allowance since Oracle
   halved it (from 4 / 24) in June 2026 — image **Ubuntu 24.04 (aarch64)**,
   public IP assigned. If the region reports no A1 capacity,
   retry off-peak or pick another home region at signup — capacity, not
   configuration, is the only hard part of Oracle's free tier.
2. **Cloud firewall.** In the instance's VCN security list, add ingress
   rules for TCP 80 and 443 from `0.0.0.0/0` (22 is already there).
3. **Bootstrap.**

   ```bash
   ssh ubuntu@<public-ip>
   git clone https://github.com/rsaim/jobd && cd jobd
   demo/bootstrap.sh                     # or: demo/bootstrap.sh demo.example.com
   ```

   A fresh VM needs one logout/login mid-bootstrap (the docker group);
   the script says so and is safe to re-run. Without an argument it serves
   on `<public-ip>.sslip.io` — a real certificate, zero DNS setup. With a
   hostname argument, point an A record at the VM first.
4. **Nightly reset.**

   ```bash
   crontab demo/cron        # edit the path inside first if not ~/jobd
   ```
5. **Verify.** Open `https://<domain>` — the landing form, one click on
   the prefilled login, the Today dashboard. The OS firewall step already
   happened in bootstrap (Oracle's Ubuntu images reject 80/443 at the host
   even after the security list allows them — the classic trap).

## Deploying updates

```bash
cd ~/jobd && git pull && demo/demo-restart.sh --build
```

## When the instance is live

Add the button to the README header's badge row:

```html
<a href="https://<domain>"><img src="https://img.shields.io/badge/live%20demo-demo%20%2F%20demo-1a56db" alt="Live demo"></a>
```

and swap `<domain>` into the nav row's Quickstart section if you want the
demo link in prose too.

## What visitors cannot do

No real mail exists to leak, and the risky surfaces fail closed without
credentials that the VM simply does not have: the chat dock answers 404
with no `JOBD_CHAT_MODEL` key, the Sync button errors with no Gmail
account, LinkedIn push answers 503 with no token, and no `OPENROUTER_API_KEY`
means nothing can spend money. The database and app ports are not
published; Caddy is the only ingress. See [SECURITY.md](../SECURITY.md)
for the full accounting.
