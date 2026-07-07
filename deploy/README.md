# Server deployment (single Linux VM + Docker Compose)

Moves the whole system off the Mac: scheduled scans, the FastAPI mobile server, the
Cloudflare tunnel (`gilhadas-stocks.com`), and the real-time surge monitor. Runs
**data-only** — no IB Gateway; the scanner falls back to Alpaca/yfinance automatically.

Architecture (`compose.yaml`, one built image, three services):

| Service        | What it runs                                             |
|----------------|----------------------------------------------------------|
| `scanner-cron` | `supercronic docker/crontab` — all scans + surge monitor |
| `api`          | `uvicorn api.server:app` on `:8000`                      |
| `cloudflared`  | Cloudflare tunnel → `http://api:8000`                    |

State lives in named volumes (`scanner_output`, `input_data`) initialised from the
image on first run, so ownership is correct and data survives rebuilds. Portfolio and
signals also sync to S3 (`stocks-breakout-scanner-s3-bucket`) when AWS keys are set.

---

## 1. Provision the VM
- ~2 vCPU / **4 GB RAM** / ~30 GB disk (torch+transformers image is multi-GB; FinBERT
  needs headroom). eu-central-1 matches the S3 bucket region; any region works.
- Install Docker Engine + the Compose plugin. Host timezone is irrelevant — the
  container sets `TZ=America/New_York`.

## 2. Get the code + secrets onto the box
```bash
git clone https://github.com/gilhadas/stocksBreakout && cd stocksBreakout
cp deploy/.env.example .env      # then fill in secrets (see the file's comments)
```
Keep **static AWS keys in `.env`** (not just an IAM role) so `utils._is_cloud()` enables
S3 sync. `.env` is gitignored + dockerignored.

## 3. Set up the Cloudflare tunnel (new tunnel, not the Mac's)
The Mac's `stocksbreakout` tunnel also serves `expenses.gilhadas-stocks.com` (a different
app that is NOT moving). So create a fresh tunnel for the stock hostnames and leave
`expenses` on the Mac.
```bash
cloudflared tunnel login
cloudflared tunnel create stocksbreakout-server        # prints a <UUID> + creds JSON
cloudflared tunnel route dns stocksbreakout-server server.gilhadas-stocks.com  # temp validation host

mkdir -p deploy/cloudflared
cp ~/.cloudflared/<UUID>.json deploy/cloudflared/
cp deploy/cloudflared/config.yml.example deploy/cloudflared/config.yml
# edit config.yml: set tunnel/credentials-file to <UUID>, and for now uncomment the
# `server.gilhadas-stocks.com` ingress block (keep prod hostnames too — they only go
# live once you route their DNS in step 6).
```

## 4. Build and start (validation mode — no scheduled mutations yet)
Bring up only the API + tunnel first, so the server does **not** run the schedule (which
would double-write S3 `portfolio.json` and double-send alerts while the Mac is still live):
```bash
docker compose up -d --build api cloudflared
```

## 5. Validate on the temp hostname
```bash
# API is up (expect 401/422, NOT 404/connection refused):
curl -s -o /dev/null -w "%{http_code}\n" https://server.gilhadas-stocks.com/portfolio

# Data-only scan works and writes a signals CSV (expect the log line
# "No real IB connection — using Alpaca/yfinance"). --no-notify avoids double alerts:
docker compose run --rm scanner-cron \
  python3 breakout_scanner.py input/all.txt --mode swing --cron --no-notify
docker compose run --rm scanner-cron ls -la scanner_output/signals

# S3 sync fired (portfolio/signals visible under the bucket from this box).
# FinBERT: first --sentiment scan downloads the model once into the volume, then reuses it.
```
Point the mobile app at `server.gilhadas-stocks.com` and confirm login + a portfolio load.

## 6. Migrate the auth DB, then cut over
Portfolio state comes from S3 automatically, but the `trading_api_kit` auth DB
(`scanner_output/users.db` — mobile logins + Expo push tokens) is local-only. Copy it
**with the api stopped** — overwriting a SQLite file while uvicorn holds it open can
corrupt it, and a running api won't see the new rows until it restarts:
```bash
# On the Mac: scp scanner_output/users.db <vm>:~/stocksBreakout/users.db
docker compose stop api
docker compose cp ./users.db api:/app/scanner_output/users.db   # lands in the shared volume
docker compose start api
```
Then cut over:
```bash
# Route the production hostnames onto the server tunnel:
cloudflared tunnel route dns stocksbreakout-server gilhadas-stocks.com
cloudflared tunnel route dns stocksbreakout-server api.gilhadas-stocks.com
# (You can now remove the temp server.gilhadas-stocks.com ingress line.)

# Start the full stack incl. the schedule:
docker compose up -d
```

## 7. Decommission the Mac
```bash
# On the Mac:
launchctl bootout gui/$(id -u)/com.stocksbreakout.api
launchctl bootout gui/$(id -u)/com.stocksbreakout.tunnel   # leaves expenses tunnel? see note
pkill -f cron_agent.py ; pkill -f signal_surge_monitor.py
```
> Note: if the Mac's tunnel still needs to serve `expenses.gilhadas-stocks.com`, keep
> `com.stocksbreakout.tunnel` running and instead remove only the two stock hostnames
> from its `~/.cloudflared/config.yml` (they're now served by the server tunnel).

Keep the Mac idle as a fallback for a few days before fully retiring it.

---

## Operations
```bash
docker compose logs -f scanner-cron          # tail the scheduler
docker compose logs -f api                   # tail the API
docker compose restart api                   # restart one service
docker compose up -d --build                 # rebuild + roll after a code change
docker compose run --rm scanner-cron \       # ad-hoc scan
  python3 breakout_scanner.py input/all.txt --mode swing --cron --no-notify
```
Cron/scan logs also land in `scanner_output/logs/` inside the `scanner_output` volume.

## Notes
- **Data-only:** `connect_to_ib()` fails instantly on the refused `127.0.0.1:7497` and
  the orchestrator uses Alpaca/yfinance. Only `scalping` mode requires IB; the crontab
  never runs it.
- **macOS notifications** self-disable off-Darwin (`notifier.mac_native_enabled`);
  Discord/Telegram/email are the live channels.
- **`cron_jobs.txt` / `restart_all.sh` are Mac-only** and unused here — the server
  scheduler is `docker/crontab` (run by supercronic).
