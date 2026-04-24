# After Code Change — Deployment Checklist

## 1. Python backend changes
**Files:** `api/server.py`, `auto_portfolio.py`, `config.py`, `api/auth.py`, `api/models.py`, or any module imported by the server.

```bash
# Kill the running uvicorn — launchd restarts it automatically within ~5s
kill $(lsof -ti:8000)

# Verify new process is up — expect 401 (auth-gated), NOT 404
sleep 5
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8000/portfolio/reset
```

Check logs if something breaks:
```bash
tail -30 scanner_output/api_server.log   # request log
tail -30 scanner_output/api_server.err   # errors / tracebacks
```

Diagnose stale process (files newer than the running server):
```bash
ps -o lstart= -p $(lsof -ti:8000)
stat -f %Sm api/server.py auto_portfolio.py
```

---

## 2. Mobile app (Expo web) changes
**Files:** anything under `mobile/` — `mobile/app/**`, `mobile/lib/api.ts`, `mobile/components/**`, etc.

```bash
cd mobile
npx expo export --platform web --output-dir ../api/static
```

No server restart needed — FastAPI serves the static bundle directly.
The update is live immediately after the export finishes.

---

## 3. Streamlit app changes
**Files:** `pages/*.py`, `auto_portfolio.py`, `config.py`

Streamlit caches module imports. To pick up changes, either:
- **Hard-reload** the browser tab (Cmd+Shift+R), **or**
- Restart the Streamlit process if it's running as a daemon.

If `auto_portfolio.py` changed and the API server was also restarted, Streamlit
will get fresh data on the next page load regardless (it calls the API).

---

## 4. Database / auth changes
**Files:** `api/database.py`, `api/models.py`, new migration needed.

```bash
# Tables are created on startup via create_tables() — a server restart is enough
# for additive schema changes (new columns with defaults, new tables).
# For destructive changes, back up first:
cp scanner_output/portfolio/users.db scanner_output/portfolio/users.db.bak
kill $(lsof -ti:8000)
```

---

## 5. Price cache
`scanner_output/portfolio/entry_price_cache.json` — disk-backed yfinance cache.

- **Automatic**: populated on each `recalculate()` call, survives server restarts.
- **Force refresh**: delete the file and restart the server.

```bash
rm scanner_output/portfolio/entry_price_cache.json
kill $(lsof -ti:8000)
```

---

## 6. Quick sanity check after any restart

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"<your-email>","password":"<your-password>"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Portfolio summary
curl -s http://127.0.0.1:8000/portfolio \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool | head -20

# Recalculate (background job)
JOB=$(curl -s -X POST http://127.0.0.1:8000/portfolio/recalculate \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"min_date":"2026-01-01"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")
echo "Job: $JOB"
sleep 5
curl -s http://127.0.0.1:8000/portfolio/recalculate/status/$JOB \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

---

## 7. Common pitfalls

| Symptom | Cause | Fix |
|---|---|---|
| New endpoint returns 404 | Server not restarted after `server.py` edit | `kill $(lsof -ti:8000)` |
| `AttributeError` on running process | `auto_portfolio.py` changed, server stale | Same as above |
| Mobile shows old UI | Forgot to re-export | `npx expo export --platform web --output-dir ../api/static` |
| Streamlit shows `KeyError` | Old trade records missing new fields | Add `.get('field', default)` fallback |
| `ModuleNotFoundError` in Streamlit | Streamlit uses system Python, not venv | `pip install <module>` in system Python |
| Recalculate slow every time | `entry_price_cache.json` deleted or never written | Check file exists; first run is always slow |
