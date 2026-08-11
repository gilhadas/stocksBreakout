# Server Operations Guide — for humans, not sysadmins

This explains how to check on, restart, update, and fix the stocksBreakout server
without needing prior server/Docker knowledge. The deployment/architecture story is in
[README.md](README.md); this is the day-to-day manual.

> **Host as of 2026-08-02: an Oracle Cloud VM, not AWS EC2.** Production moved off EC2
> (CLAUDE.md §25). The old EC2 instance is **stopped, not terminated**, and its two
> CloudWatch alarms sit permanently in `ALARM` — expected and harmless (their reboot/
> recover actions are no-ops on a stopped instance, they will not start it), but the AWS
> console will show red for it indefinitely. Ignore it; nothing there serves traffic.

---

## 1. The big picture — what you actually have

- **A cloud computer** (an Oracle Cloud VM) at `82.70.210.194` runs everything, 24/7.
  Your Mac no longer runs any part of the stock system.
- On that computer, **Docker** runs six *containers*. A container is like a sealed
  lunchbox: the app plus everything it needs (Python, libraries, settings) packed
  together, isolated from the rest of the machine.

| Container         | What it does                                                       |
|-------------------|--------------------------------------------------------------------|
| `sb-api`          | The backend for the web/mobile app (login, portfolio, buy/sell)    |
| `sb-scanner-cron` | The clock. Runs all scheduled scans and sends Telegram/Email alerts |
| `sb-dashboard`    | The Streamlit admin/scanning UI (`dashboard.gilhadas-stocks.com`)  |
| `sb-journal`      | The Trade Journal single-page app (`journal.gilhadas-stocks.com`)  |
| `sb-cloudflared`  | The doorway. Makes the `gilhadas-stocks.com` names reach this server |
| `sb-tailscale`    | Private-network access (a second way in if the tunnel breaks)      |

- **Data that must survive** (login database, signals, logs, the FinBERT model cache)
  lives in Docker *volumes* — storage that persists even when containers are rebuilt.
  Portfolio and signals also sync to S3 as an off-site copy.

Two terms you'll see constantly:
- **Image** = the recipe (built from the code).
- **Container** = a running instance of that recipe.
- `docker compose` = the remote control that starts/stops/rebuilds all of them
  according to `compose.yaml`.

### ⚠ This box runs a SECOND, unrelated system

The same VM also hosts the **DayTrade** stack as a separate Docker Compose project:

```
NAME             STATUS         CONFIG FILES
daytrade         running(4)     /opt/daytrade/docker-compose.yml
stocksbreakout   running(6)     /home/ubuntu/stocksBreakout/compose.yaml
```

Consequences you must respect:

1. **`docker compose` acts on whichever project your current directory belongs to.**
   Always `cd ~/stocksBreakout` first. Running it from `/opt/daytrade` (or from your
   home directory with no compose file) will not do what you expect.
2. **Whole-machine Docker commands hit BOTH systems.** `docker system prune`,
   `docker stop $(docker ps -q)` and similar are not scoped to stocksBreakout. See
   section 8.
3. `docker compose ls` is the safe way to see both projects at once.

---

## 2. Connecting to the server

From the Mac's terminal:

```bash
ssh -i ~/.ssh/daytrade_oracle ubuntu@82.70.210.194
cd ~/stocksBreakout
```

You are now typing commands *on the server*. Type `exit` to come back to the Mac.
Every command below assumes you're on the server inside `~/stocksBreakout`,
unless marked **(on the Mac)**.

> The key name says `daytrade` because this VM was originally provisioned for that
> system; it is the correct key for this box. Unlike the retired EC2 box (which was
> reachable **only** over Tailscale, because its security group had zero inbound
> rules), this one accepts SSH directly on its public IP.

---

## 3. Daily health check (30 seconds)

```bash
cd ~/stocksBreakout && docker compose ps
```

All six services should say **`Up`** (e.g. `Up 5 days`). Two bad signs:

- **`Restarting (1) 45 seconds ago`** — the container is crash-looping. It looks
  alive but nothing inside is working. Go to section 7.
- A service missing from the list entirely — start it: `docker compose up -d`

Quick functional checks:

```bash
# The API answers (401 = healthy, it just wants a login; 404/000 = problem)
curl -s -o /dev/null -w "%{http_code}\n" https://api.gilhadas-stocks.com/portfolio

# Disk space (keep "Use%" under ~85%)
df -h /
```

Disk is also checked automatically: `deploy/disk-alert.sh` runs hourly from the
**host** crontab (not a container) and pings a Healthchecks.io monitor, so a filling
disk pages you without anyone logging in. Confirm it with `crontab -l`.

---

## 4. Reading logs (what happened?)

```bash
docker compose logs api --tail 50            # last 50 lines from the API
docker compose logs scanner-cron --tail 50   # last 50 lines from the scheduler
docker compose logs -f scanner-cron          # live view — Ctrl+C to stop watching
```

The scans also write their own logs inside the shared volume:

```bash
docker compose exec api ls -la /app/scanner_output/logs/
docker compose exec api tail -50 /app/scanner_output/logs/cron_swing.log
```

If `scanner_output/logs/` has no file dated today (on a weekday), scheduled scans
are not running — check `docker compose ps` for a crash loop.

> ⚠ **`cron_*.log` only contains errors.** `breakout_scanner.py` sets its *stderr*
> handler to `ERROR`, so all INFO output — including the `[MEM]` probes — goes only to
> the dated `scanner_output/logs/scanner_YYYYMMDD.log`. A successful run can therefore
> look like an empty/failed one if you read the wrong file.

---

## 5. Restarting things

| Situation | Command |
|---|---|
| One service acting weird | `docker compose restart api` |
| You changed `.env` | `docker compose up -d` *(restart is NOT enough — see below)* |
| You changed code | see section 6 — needs a rebuild |
| Restart everything | `docker compose restart` |

**Why `restart` isn't enough after a `.env` change:** environment variables are
baked into the container when it's *created*. `restart` reuses the old container;
`up -d` notices the change and recreates it.

**The crontab is baked into the image too.** Changing `docker/crontab` — including the
`HC_UUID_*` healthcheck names it references — needs a full
`docker compose up -d --build scanner-cron`, not a restart.

---

## 6. Deploying a code change

Code flows in one direction: **edit on the Mac → commit → push to GitHub → pull on
the server → rebuild**. Never edit code files directly on the server (the only
exception is `.env`, which isn't in git).

```bash
# 1. (on the Mac) commit and push your change
git add <files> && git commit -m "..." && git push

# 2. (on the server)
cd ~/stocksBreakout
git pull --ff-only
docker compose up -d --build     # rebuilds the image, restarts what changed (takes a few minutes)

# 3. Verify
docker compose ps                # everything Up?
docker compose logs api --tail 20
```

Rebuild **every** service that shares the image when you touch shared code such as
`utils.py` — `api`, `scanner-cron` and `dashboard` all run the same image:

```bash
docker compose up -d --build api scanner-cron dashboard
```

### Special case: the web app (`mobile/dist/`)

The web page at `gilhadas-stocks.com` is a *pre-built* bundle that is **not stored
in git**. Changing anything under `mobile/` or `trading_api_kit/ts_client/`
requires rebuilding it on the Mac and shipping it over:

```bash
# (on the Mac)
cd ~/Documents/GitHub/stocksBreakout/mobile
npx expo export -p web                       # rebuilds mobile/dist/
scp -i ~/.ssh/daytrade_oracle -r dist/. \
    ubuntu@82.70.210.194:~/stocksBreakout/mobile/dist/

# (on the server)
docker compose up -d --build api
```

> ⚠ **Do not `tar` the bundle on macOS.** macOS `tar` embeds `._*` AppleDouble sidecar
> files (from the `com.apple.provenance` xattr) — a 40-file build arrived as 105 files
> once. `.dockerignore` covers `.DS_Store` but not `._*`, so they get baked into the
> image and served by `StaticFiles`. `scp -r` as above is safe.

### Special case: secrets (`.env`)

`.env` holds passwords/API keys and is deliberately **not in git**. To change one:

```bash
# 1. (on the Mac) edit .env in your editor — never paste secrets into chats/commits
# 2. (on the Mac) copy it over:
scp -i ~/.ssh/daytrade_oracle .env ubuntu@82.70.210.194:~/stocksBreakout/.env
# 3. (on the server) recreate containers so they pick it up:
docker compose up -d
```

---

## 7. Troubleshooting cheat sheet

**"The web app / mobile app can't connect"**
1. `docker compose ps` — is `sb-api` Up?
2. `curl -s -o /dev/null -w "%{http_code}\n" https://api.gilhadas-stocks.com/portfolio`
   — 401 is good. 502 = api container down. 000/timeout = tunnel (`sb-cloudflared`) down.
3. `docker compose logs api --tail 50` — look for a Python traceback near the end.

**"I'm not getting notifications"**
1. `docker compose ps` — is `sb-scanner-cron` **Up** (not Restarting)? This exact
   failure happened once: the container crash-looped silently and zero scans ran.
2. Check today's scan log exists: `docker compose exec api ls -la /app/scanner_output/logs/`
   Then read it: `docker compose exec api tail -30 /app/scanner_output/logs/cron_swing.log`.
   Look especially for `Failed to load watchlist ... No such file or directory` — Linux
   filenames are **case-sensitive** (`all.txt` ≠ `ALL.txt`), unlike the Mac. This exact
   mistake silently killed every scheduled scan for a week once.
3. Remember: alerts only fire when a scan actually finds signals. To force a test:
   ```bash
   docker compose run --rm scanner-cron python3 -c \
     "from notifier import Notifier; Notifier().send_all('Test','Manual test',force=True)"
   ```

**"A Healthchecks monitor says DOWN but the logs look fine"**
Read the job's exit status, not the scheduler's. Cron lines end with
`; curl .../${UUID}/$?` so the ping carries the real exit code — `/137` means the job
was **SIGKILLed**, almost always the container's memory cap. Check with
`dmesg | grep -i oom` on the host. (An older `&& curl ... || true` form hid this by
never pinging on failure *and* reporting "job succeeded" — see CLAUDE.md §15.)

**"A container keeps saying Restarting"**
```bash
docker compose logs <service> --tail 50    # the crash reason is in the last lines
```
Fix the cause (often code or config), then `docker compose up -d --build`.

**"No space left on device" (usually during a build)**
```bash
docker system df            # see what's eating space
docker builder prune -af    # deletes old build cache — always safe, frees GBs
```

**"I changed code but the server behaves like the old version"**
```bash
git log --oneline -1        # on the server — same commit as GitHub?
```
If it's behind: `git pull --ff-only && docker compose up -d --build`. Containers only
pick up code at build time — pulling alone changes nothing until you rebuild.

---

## 8. Danger zone — commands to avoid

| Never run | Why |
|---|---|
| `docker compose down -v` | The `-v` **deletes the volumes**: login database, logs, model cache. Plain `down` (no `-v`) is OK. |
| `docker system prune -a` | Not scoped to this project — it will also strip the **DayTrade** stack sharing this box. `docker builder prune -af` (build cache only) is the safe one. |
| `docker compose ...` from the wrong directory | Two compose projects live here. Always `cd ~/stocksBreakout` first. |
| `git reset` / editing code on the server | The server is a mirror of GitHub. Fix things on the Mac and push. |
| Re-enabling the Mac's scheduler | Two schedulers = duplicate alerts and conflicting S3 writes. The Mac's cron/launchd services were deliberately turned off. `cron_jobs.txt` in the repo is **retired** — production runs `docker/crontab`. |
| Starting the old EC2 instance | It still has the full stack installed. Two live deployments would double-write S3 and double-send alerts. |

---

## 9. Occasional maintenance (monthly-ish)

```bash
df -h /                     # disk under ~85%?
docker builder prune -af    # clear build cache
docker compose ps           # everything still Up?
free -h                     # memory headroom (this box has 11 GiB; ~2 GiB in use is normal)
```

Backup of the login database (the one thing not in git and not in S3 — it is
**per-box**, so the copy in the repo working tree is a stale Mac artifact, never the
source of truth):

```bash
# (on the server) copy it out of the volume first
docker compose exec api cat /app/scanner_output/users.db > ~/users.db.$(date +%Y%m%d)

# (on the Mac)
scp -i ~/.ssh/daytrade_oracle \
    ubuntu@82.70.210.194:~/users.db.$(date +%Y%m%d) ~/Backups/
```

---

## 10. Quick reference card

```bash
# connect
ssh -i ~/.ssh/daytrade_oracle ubuntu@82.70.210.194
cd ~/stocksBreakout

# health
docker compose ps
docker compose logs -f scanner-cron
df -h /

# restart / apply .env change
docker compose restart api
docker compose up -d

# deploy code (rebuild every service sharing the image)
git pull --ff-only && docker compose up -d --build api scanner-cron dashboard

# free disk (build cache only — NOT `docker system prune -a`, DayTrade shares this box)
docker builder prune -af

# manual scan (with alerts / silent)
docker compose run --rm scanner-cron python3 breakout_scanner.py input/all.txt --mode swing --cron --notify
docker compose run --rm scanner-cron python3 breakout_scanner.py input/all.txt --mode swing --cron --no-notify
```
