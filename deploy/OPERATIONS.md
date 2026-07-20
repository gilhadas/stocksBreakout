# Server Operations Guide — for humans, not sysadmins

This explains how to check on, restart, update, and fix the stocksBreakout server
without needing prior server/Docker knowledge. The one-time setup story is in
[README.md](README.md); this is the day-to-day manual.

---

## 1. The big picture — what you actually have

- **A cloud computer** (an AWS "EC2 instance") at `3.73.144.233` runs everything,
  24/7. Your Mac no longer runs any part of the stock system.
- On that computer, **Docker** runs three *containers*. A container is like a
  sealed lunchbox: the app plus everything it needs (Python, libraries, settings)
  packed together, isolated from the rest of the machine.

| Container        | What it does                                                        |
|------------------|---------------------------------------------------------------------|
| `sb-api`         | The backend for the web/mobile app (login, portfolio, buy/sell)     |
| `sb-scanner-cron`| The clock. Runs all scheduled scans and sends Telegram/Email alerts |
| `sb-cloudflared` | The doorway. Makes `gilhadas-stocks.com` reach this server          |

- **Data that must survive** (login database, signals, logs, the FinBERT model
  cache) lives in Docker *volumes* — storage that persists even when containers
  are rebuilt. Portfolio and signals also sync to S3 as an off-site copy.

Two terms you'll see constantly:
- **Image** = the recipe (built from the code).
- **Container** = a running instance of that recipe.
- `docker compose` = the remote control that starts/stops/rebuilds all of them
  according to `compose.yaml`.

---

## 2. Connecting to the server

From the Mac's terminal:

```bash
ssh -i ~/.ssh/stocksbreakout-key.pem ubuntu@3.73.144.233
cd stocksBreakout
```

You are now typing commands *on the server*. Type `exit` to come back to the Mac.
Every command below assumes you're on the server inside `~/stocksBreakout`,
unless marked **(on the Mac)**.

---

## 3. Daily health check (30 seconds)

```bash
docker compose ps
```

All three services should say **`Up`** (e.g. `Up 3 days`). Two bad signs:

- **`Restarting (1) 45 seconds ago`** — the container is crash-looping. It looks
  alive but nothing inside is working. Go to section 7.
- A service missing from the list entirely — start it: `docker compose up -d`

Quick functional checks:

```bash
# The API answers (401 = healthy, it just wants a login; 404/000 = problem)
curl -s -o /dev/null -w "%{http_code}\n" https://gilhadas-stocks.com/portfolio

# Disk space (keep "Use%" under ~85%)
df -h /
```

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
git pull
docker compose up -d --build     # rebuilds the image, restarts what changed (takes a few minutes)

# 3. Verify
docker compose ps                # everything Up?
docker compose logs api --tail 20
```

### Special case: the web app (`mobile/dist/`)

The web page at `gilhadas-stocks.com` is a *pre-built* bundle that is **not stored
in git**. Changing anything under `mobile/` or `trading_api_kit/ts_client/`
requires rebuilding it on the Mac and shipping it over:

```bash
# (on the Mac)
cd ~/Documents/GitHub/stocksBreakout/mobile
npx expo export -p web                       # rebuilds mobile/dist/
scp -i ~/.ssh/stocksbreakout-key.pem -r dist/. \
    ubuntu@3.73.144.233:~/stocksBreakout/mobile/dist/

# (on the server)
docker compose up -d --build api
```

### Special case: secrets (`.env`)

`.env` holds passwords/API keys and is deliberately **not in git**. To change one:

```bash
# 1. (on the Mac) edit .env in your editor — never paste secrets into chats/commits
# 2. (on the Mac) copy it over:
scp -i ~/.ssh/stocksbreakout-key.pem .env ubuntu@3.73.144.233:~/stocksBreakout/.env
# 3. (on the server) recreate containers so they pick it up:
docker compose up -d
```

---

## 7. Troubleshooting cheat sheet

**"The web app / mobile app can't connect"**
1. `docker compose ps` — is `sb-api` Up?
2. `curl -s -o /dev/null -w "%{http_code}\n" https://gilhadas-stocks.com/portfolio`
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
If it's behind: `git pull && docker compose up -d --build`. Containers only pick
up code at build time — pulling alone changes nothing until you rebuild.

---

## 8. Danger zone — commands to avoid

| Never run | Why |
|---|---|
| `docker compose down -v` | The `-v` **deletes the volumes**: login database, logs, model cache. Plain `down` (no `-v`) is OK. |
| `git reset` / editing code on the server | The server is a mirror of GitHub. Fix things on the Mac and push. |
| Re-enabling the Mac's scheduler | Two schedulers = duplicate alerts and conflicting S3 writes. The Mac's cron/launchd services were deliberately turned off. |

---

## 9. Occasional maintenance (monthly-ish)

```bash
df -h /                     # disk under ~85%?
docker builder prune -af    # clear build cache
docker compose ps           # everything still Up?
```

Backup of the login database (the one thing not in git and not in S3):

```bash
# (on the Mac)
scp -i ~/.ssh/stocksbreakout-key.pem \
    ubuntu@3.73.144.233:~/stocksBreakout/users.db ~/Backups/users.db.$(date +%Y%m%d)
```

---

## 10. Quick reference card

```bash
# connect
ssh -i ~/.ssh/stocksbreakout-key.pem ubuntu@3.73.144.233
cd stocksBreakout

# health
docker compose ps
docker compose logs -f scanner-cron
df -h /

# restart / apply .env change
docker compose restart api
docker compose up -d

# deploy code
git pull && docker compose up -d --build

# free disk
docker builder prune -af

# manual scan (with alerts / silent)
docker compose run --rm scanner-cron python3 breakout_scanner.py input/all.txt --mode swing --cron --notify
docker compose run --rm scanner-cron python3 breakout_scanner.py input/all.txt --mode swing --cron --no-notify
```
