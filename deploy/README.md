# Server deployment (single Linux VM + Docker Compose)

Runs the whole system off-Mac: scheduled scans, the FastAPI mobile server, the Streamlit
dashboard, the Trade Journal SPA, and the Cloudflare tunnel. Runs **data-only** — no IB
Gateway; the scanner falls back to Alpaca/yfinance automatically.

Day-to-day operation (health checks, logs, restarts, troubleshooting) is in
[OPERATIONS.md](OPERATIONS.md). This file is the architecture and the from-scratch build.

## Where production actually runs

Host address, SSH key path, and VM hostname live in a **private ops note**, not
this public repo. From a shell that has those values:

```bash
ssh -i "$SSH_KEY" ubuntu@"$ORACLE_HOST"
```

| | |
|---|---|
| Host | **Oracle Cloud VM** (`il-jerusalem-1`). Address: `$ORACLE_HOST` (private ops note). |
| Since | 2026-08-02 (previously AWS EC2 — see "History" below) |
| Architecture | **`aarch64` (ARM)** — matters for builds, see below |
| Resources | 11 GiB RAM, 45 GB disk, 2 GiB swapfile |
| Access | `ssh -i "$SSH_KEY" ubuntu@"$ORACLE_HOST"` — see the private ops note |
| Repo path | `~/stocksBreakout` |

⚠ **This VM also hosts an unrelated system.** The `daytrade` stack
(`/opt/daytrade/docker-compose.yml`) runs alongside as a **separate Docker Compose
project**. `docker compose ls` shows both. Always `cd ~/stocksBreakout` before running
`docker compose`, and never use unscoped commands like `docker system prune -a` —
they hit both stacks.

Architecture (`compose.yaml`, one built image shared by three Python services, six
containers total):

| Service        | Container         | What it runs                                        |
|----------------|-------------------|-----------------------------------------------------|
| `scanner-cron` | `sb-scanner-cron` | `supercronic docker/crontab` — all scans + monitors |
| `api`          | `sb-api`          | `uvicorn api.server:app` on `:8000`                 |
| `dashboard`    | `sb-dashboard`    | Streamlit admin/scanning UI on `:8501`              |
| `journal`      | `sb-journal`      | Trade Journal SPA (nginx)                           |
| `cloudflared`  | `sb-cloudflared`  | Cloudflare tunnel → the services above              |
| `tailscale`    | `sb-tailscale`    | Private-network access path                         |

Hostnames served by the tunnel: `gilhadas-stocks.com` and `api.gilhadas-stocks.com`
→ `api:8000`; `journal.` and `journal-daytrade.` → `journal:80` (one container, two
origins); `dashboard.` → `dashboard:8501`.

State lives in named volumes (`scanner_output`, `input_data`, `tailscale_state`)
initialised from the image on first run, so ownership is correct and data survives
rebuilds. Portfolio and signals also sync to S3
(`stocks-breakout-scanner-s3-bucket`) when AWS keys are set.

Each service has a `mem_limit` so a runaway container's **own** cgroup OOM killer fires
before the host killer starts picking victims. `scanner-cron` is 2048m — sized from a
measured ~1 GiB working set (FinBERT is a flat ~704 MiB resident, plus the scan itself).

---

## 1. Provision the VM
- ~2 vCPU / **4 GB RAM minimum** / ~30 GB disk (the torch+transformers image is
  multi-GB; FinBERT needs headroom). The current box is comfortably above this; the
  retired EC2 `t3.small` was *below* it and OOM-killed scans for a week (CLAUDE.md
  §15–§18).
- Install Docker Engine + the Compose plugin. Host timezone is irrelevant — the
  container sets `TZ=America/New_York`.
- Add swap (`deploy/setup-swap.sh`) as a backstop, not as headroom.

## 2. Get the code + secrets onto the box
```bash
git clone https://github.com/gilhadas/stocksBreakout && cd stocksBreakout
cp deploy/.env.example .env      # then fill in secrets (see the file's comments)
```
Keep **static AWS keys in `.env`** (not just an IAM role) so `utils._is_cloud()` enables
S3 sync. `.env` is gitignored + dockerignored.

`.env` also holds the `HC_UUID_*` Healthchecks.io dead-man-switch IDs referenced by
`docker/crontab`. They are baked into the image at build time, so adding one needs a
rebuild of `scanner-cron`, not a restart.

## 3. Cross-architecture note (ARM)
The current host is `aarch64`; the retired EC2 box was `x86_64`. This is a
**cross-architecture rebuild, not a redeploy** — a saved x86 image will not run here.

`deploy/constraints-ec2-20260802.txt` pins the exact 133-package dependency set the
x86 deployment was running, so an ARM rebuild does not silently become a dependency
upgrade at the same time. It is wired into the Dockerfile.

⚠ Do **not** feed the whole constraints file to the `torch` install step: that step's
`--index-url` is scoped to `download.pytorch.org`, which cannot serve `fsspec` (a torch
dependency) at all, and pip fails with `ResolutionImpossible`. The torch line is
extracted as an explicit target; the full constraint applies only to the normal-PyPI
step.

Re-measure `mem_limit` after an architecture change rather than copying the old number
— and take the measurement with a **generous** cap. A tight cap makes the process
thrash against the ceiling and report a *higher* peak than it actually needs (measured:
1231.8 MB under a 1500m cap vs 1002.2 MB under 2048m, same workload).

## 4. Set up the Cloudflare tunnel
```bash
cloudflared tunnel login
cloudflared tunnel create <tunnel-name>        # prints a <UUID> + creds JSON

mkdir -p deploy/cloudflared
cp ~/.cloudflared/<UUID>.json deploy/cloudflared/
cp deploy/cloudflared/config.yml.example deploy/cloudflared/config.yml
# edit config.yml: set tunnel/credentials-file to <UUID> and list the ingress hostnames
```

Two traps, both hit in production:

- ⚠ **Always pass the full UUID to `route dns`, never the tunnel name.** The name is
  matched loosely against other tunnels in the account — `stocksbreakout-oracle` matched
  an older `stocksbreakout` and routed the hostname to the wrong tunnel (2026-08-02).
- ⚠ **`--overwrite-dns` does not overwrite a CNAME that another tunnel already owns.**
  It silently no-ops and reports "already configured". Edit the CNAME target by hand in
  the Cloudflare dashboard (`DNS → <name> → <new-tunnel-id>.cfargotunnel.com`).

## 5. Build and start
```bash
docker compose up -d --build
```

If you are standing this up *in parallel* with a still-live deployment, bring up only
`api` + `cloudflared` first and point them at a temporary hostname — starting
`scanner-cron` runs the schedule, which would double-write S3 and double-send alerts.

## 6. Validate
```bash
# API is up (expect 401/422, NOT 404/connection refused):
curl -s -o /dev/null -w "%{http_code}\n" https://api.gilhadas-stocks.com/portfolio

# Data-only scan works and writes a signals CSV (expect the log line
# "No real IB connection — using Alpaca/yfinance"). --no-notify avoids alerts:
docker compose run --rm scanner-cron \
  python3 breakout_scanner.py input/all.txt --mode swing --cron --no-notify
docker compose run --rm scanner-cron ls -la scanner_output/signals
```

⚠ **Verify a cutover with a stop/start test, not `cloudflared tunnel route dns` output.**
Briefly `docker compose stop api` and curl the public hostname: **502 proves traffic is
landing on this box**. A normal response means DNS has not actually moved yet, whatever
the CLI reported.

## 7. Migrate the auth DB (only when moving hosts)
Portfolio state comes from S3 automatically, but the `trading_api_kit` auth DB
(`scanner_output/users.db` — logins + Expo push tokens) is **per-box and never synced to
S3**. Copy it **with the api stopped** — overwriting a SQLite file while uvicorn holds it
open can corrupt it, and a running api won't see the new rows until it restarts:
```bash
# from the old host: scp scanner_output/users.db <new-vm>:~/stocksBreakout/users.db
docker compose stop api
docker compose cp ./users.db api:/app/scanner_output/users.db   # lands in the shared volume
docker compose start api
```

## 8. Monitoring
- **Healthchecks.io dead-man switches** — one `HC_UUID_*` per cron job in
  `docker/crontab`. A job's ping carries its exit status (`/$?`), so a SIGKILLed scan
  reports `/137` immediately instead of waiting out the grace window.
  ⚠ One UUID cannot cover cron lines that run on genuinely different schedules; give
  each shape its own check (this bit three times — `SWING`/`VALIDATE`/`MONITOR`).
- **Disk** — `deploy/disk-alert.sh` runs hourly from the **host** crontab (not a
  container) and pings `HC_UUID_DISK`.
- **Container logs** — capped at `10m × 3` per service via the `x-logging` anchor in
  `compose.yaml`. Verified 2026-08-11 against the running containers (all six report it
  under `docker inspect`, not merely in the file), bounding this stack at 180 MB.
  ⚠ The co-tenant `daytrade` project is **uncapped**, and there is no
  `/etc/docker/daemon.json`, so anything created outside `compose.yaml` is unbounded
  too. Both land on this same disk — covered only by the hourly disk alert above.

---

## History

- **2026-07-07 → 2026-08-02: AWS EC2** (`i-015657f7d29bb673e`, `eu-central-1`).
  Reachable **only** over Tailscale — its security group had zero inbound rules. Started
  as a `t3.small`, resized to `t3.medium` mid-life chasing OOM kills whose real cause
  turned out to be a per-call S3 client leak (CLAUDE.md §15–§19).
  **The instance is stopped, not terminated.** Its two CloudWatch alarms are stuck in
  `ALARM` — harmless (reboot/recover are no-ops on a stopped instance) but permanently
  red in the console. Do not start it: two live deployments would double-write S3 and
  double-send alerts.
- **Before that: a Mac.** `cron_jobs.txt` and `restart_all.sh` are that era's artifacts
  and are **retired** — kept only as the reference `tests/test_crontab_parity.py` diffs
  against. Production runs `docker/crontab` under supercronic.
  `expenses.gilhadas-stocks.com` is a *different* app that never moved off the Mac.

## Notes
- **Data-only:** `connect_to_ib()` fails instantly on the refused `127.0.0.1:7497` and
  the orchestrator uses Alpaca/yfinance. Only `scalping` mode requires IB; the crontab
  never runs it.
- **macOS notifications** self-disable off-Darwin (`notifier.mac_native_enabled`);
  Discord/Telegram/email are the live channels.
- **Unresolved:** whether `sb-tailscale` is still load-bearing now that direct SSH
  works.
