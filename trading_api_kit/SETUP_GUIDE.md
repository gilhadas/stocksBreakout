# trading-api-kit — Setup Guide

How to move `trading_api_kit` from stocksBreakout into a new scanner project.

---

## Overview

`trading_api_kit` is a reusable library that provides:
- JWT auth (email/password + Google OAuth)
- User management (SQLite or PostgreSQL)
- Expo push notification registry
- Admin dashboard
- FastAPI app factory

Your new project only needs to add its own scanner-specific endpoints on top.

---

## Step 1 — Create the New Repo

```bash
mkdir ~/Documents/GitHub/my-new-scanner
cd ~/Documents/GitHub/my-new-scanner

git init
git branch -M main

# Create on GitHub (requires gh CLI)
gh repo create my-new-scanner --private --source=. --remote=origin
```

> **Do NOT remove trading_api_kit from stocksBreakout yet.**
> Keep it in both until the new project is confirmed working.

---

## Step 2 — Copy the Library Files

```bash
# Copy the Python library
cp -r ~/Documents/GitHub/stocksBreakout/trading_api_kit \
      ~/Documents/GitHub/my-new-scanner/trading_api_kit

# Copy the mobile app (optional — only if you need the Expo app)
cp -r ~/Documents/GitHub/stocksBreakout/mobile \
      ~/Documents/GitHub/my-new-scanner/mobile
```

**What you copy:**
| Folder | What it is |
|--------|-----------|
| `trading_api_kit/` | Python library: auth, JWT, users, push notifications |
| `mobile/` | Expo React Native app (optional) |

**What you do NOT copy** (stocksBreakout-specific, write your own):
- `scanner.py`, `config.py`, `auto_portfolio.py`
- `api/server.py` — you write a new one in Step 5

---

## Step 3 — Set Up the New Project Structure

```bash
cd ~/Documents/GitHub/my-new-scanner

# Create the API folder
mkdir -p api
touch api/__init__.py
touch api/server.py      # ← you write this in Step 5

# Create a Python virtual environment
python3 -m venv venv

# Activate it (run this every time you open a new terminal)
source venv/bin/activate    # Mac / Linux
# venv\Scripts\activate     # Windows
```

You'll know it's active when you see `(venv)` at the start of your terminal prompt.

---

## Step 4 — Install the Library with pip

```bash
# Make sure venv is active first
source venv/bin/activate

# Install trading_api_kit and all its dependencies
pip install ./trading_api_kit

# Install the web server
pip install fastapi uvicorn[standard]
```

**What `pip install ./trading_api_kit` does:**
- Reads `trading_api_kit/pyproject.toml`
- Installs the package into your venv so you can `from trading_api_kit import ...`
- Also installs all dependencies: `sqlalchemy`, `PyJWT`, `bcrypt`, `httpx`, `python-dotenv`

> Run pip in the **new project** (`my-new-scanner`), not in stocksBreakout.
> Each project has its own venv.

---

## Step 5 — Write `api/server.py`

Create `my-new-scanner/api/server.py`:

```python
import sys
from pathlib import Path
from fastapi import Depends
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# trading_api_kit provides auth, users, push — mount with one call
from trading_api_kit import create_app, get_current_user
from trading_api_kit.models import User

app = create_app(
    title="MyNewScanner API",
    version="1.0.0",
    static_dir=Path(__file__).resolve().parent / "static",  # remove if no web UI
)

# ── Add your scanner-specific endpoints below ─────────────────────────────────

@app.get("/portfolio")
def get_portfolio(user: User = Depends(get_current_user)):
    # Replace with your own portfolio logic
    return {"user": user.email, "positions": []}

@app.get("/signals")
def get_signals(user: User = Depends(get_current_user)):
    # Replace with your own signal scanning logic
    return {"signals": []}
```

`create_app()` automatically mounts:
- `POST /auth/login` — email + password login
- `GET  /auth/me` — current user info
- `GET  /auth/google` — Google OAuth redirect
- `GET  /auth/google/callback` — Google OAuth callback
- `GET  /admin` — HTML user management dashboard
- `GET/POST/DELETE /admin/users` — user CRUD API

---

## Step 6 — Create the `.env` File

Create `my-new-scanner/.env` (never commit this file):

```env
# ─── REQUIRED ────────────────────────────────────────────────────────────────

# JWT signing secret — generate with:
# python -c "import secrets; print(secrets.token_hex(32))"
API_SECRET_KEY=paste-your-random-secret-here


# ─── USERS ───────────────────────────────────────────────────────────────────

# Option A: Single password (one user, simplest setup)
APP_PASSWORD=your-app-password
DEFAULT_USER_EMAIL=you@example.com
DEFAULT_USER_ID=00000000-0000-0000-0000-000000000001

# Option B: Multi-user (create users via /admin dashboard)
# No extra env needed — just set ADMIN_SECRET below and add users there


# ─── OPTIONAL — Google OAuth ──────────────────────────────────────────────────
# Get from: Google Cloud Console → APIs & Services → Credentials

GOOGLE_CLIENT_ID=xxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxx

# Where Google redirects after login
# Local dev:   http://localhost:8000/auth/google/callback
# Production:  https://your-domain.com/auth/google/callback
GOOGLE_REDIRECT_URI=http://localhost:8000/auth/google/callback

# Your mobile app's custom URL scheme (must match app.json)
MOBILE_APP_SCHEME=mynewscanner


# ─── OPTIONAL — Admin dashboard ──────────────────────────────────────────────

# Required to access /admin — enter this when the browser prompts you
ADMIN_SECRET=pick-a-strong-admin-password


# ─── OPTIONAL — Database ─────────────────────────────────────────────────────

# Default: SQLite at scanner_output/users.db (auto-created, no setup needed)
# PostgreSQL example:
# DATABASE_URL=postgresql://user:password@localhost:5432/mynewscanner


# ─── OPTIONAL — Push notifications ───────────────────────────────────────────

PUSH_TOKEN_FILE=scanner_output/.expo_push_tokens.json


# ─── OPTIONAL — CORS ─────────────────────────────────────────────────────────

# Default: first-party origins only (never '*' — credentials are always on)
# CORS_ORIGINS=https://your-domain.com,https://app.your-domain.com
```

Add `.env` to `.gitignore`:

```bash
echo ".env" >> .gitignore
echo "venv/" >> .gitignore
echo "scanner_output/" >> .gitignore
echo "__pycache__/" >> .gitignore
```

---

## Step 7 — Run the Server

```bash
cd ~/Documents/GitHub/my-new-scanner
source venv/bin/activate

uvicorn api.server:app --host 0.0.0.0 --port 8000 --reload
```

**Test it's working:**

```bash
# Should return 401 (not 404) — server is up, auth is working
curl http://localhost:8000/portfolio

# Login and get a token
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"your-app-password"}'

# Open admin dashboard in browser
open http://localhost:8000/admin
```

---

## Step 8 — Configure the Mobile App

In `mobile/lib/api.ts` or `app/_layout.tsx`, set the base URL at startup:

```typescript
import { configure } from '../lib/api';

// Local dev
await configure({ baseUrl: 'http://localhost:8000' });

// Production
await configure({ baseUrl: 'https://your-new-scanner-domain.com' });
```

Update `app.json` to match `MOBILE_APP_SCHEME`:

```json
{
  "expo": {
    "scheme": "mynewscanner"
  }
}
```

---

## Step 9 — Should You Remove it from stocksBreakout?

**No, keep it in both for now.** Options for long-term sharing:

| Approach | When to use |
|----------|------------|
| **Copy in both repos** (current) | Projects evolve independently — simplest |
| **Install from GitHub** | Single source of truth, both projects stay in sync |
| **Publish to PyPI** | If you plan to share publicly or with a team |

**To install directly from GitHub (no copying needed):**

```bash
pip install "git+https://github.com/gilhadas/stocksBreakout#subdirectory=trading_api_kit"
```

Then in any project:

```python
from trading_api_kit import create_app, get_current_user
```

---

## Final Project Structure

```
my-new-scanner/
├── .env                          ← secrets (never commit)
├── .gitignore
├── venv/                         ← Python virtual environment
├── trading_api_kit/              ← copied from stocksBreakout
│   ├── __init__.py
│   ├── auth.py
│   ├── auth_routes.py            ← /auth/* endpoints
│   ├── admin_routes.py           ← /admin/* endpoints + dashboard
│   ├── models.py                 ← User table
│   ├── database.py               ← SQLite / PostgreSQL engine
│   ├── deps.py                   ← get_current_user dependency
│   ├── factory.py                ← create_app()
│   ├── push_registry.py          ← Expo push tokens
│   ├── config.py                 ← reads all env vars
│   ├── pyproject.toml            ← pip metadata
│   └── ts_client/src/            ← TypeScript client
│       ├── client.ts             ← configure(), authFetch()
│       ├── auth.ts               ← loginWithEmail(), logout()
│       ├── notifications.ts      ← registerForPushNotifications()
│       └── index.ts              ← public exports
├── api/
│   ├── __init__.py
│   └── server.py                 ← YOUR scanner endpoints
├── mobile/                       ← Expo app (optional)
│   └── lib/api.ts                ← configure() with your URL
└── scanner_output/               ← auto-created: users.db, logs
```

---

## Quick Reference — `.env` Required vs Optional

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `API_SECRET_KEY` | ✅ Yes | *(none — refuses documented default)* | JWT signing secret |
| `APP_PASSWORD` | If single-user | — | Legacy password login (documented example values refused) |
| `DEFAULT_USER_EMAIL` | If single-user | — | Auto-created user email |
| `DEFAULT_USER_ID` | If single-user | — | Auto-created user UUID |
| `ADMIN_SECRET` | For /admin | — | Admin dashboard password |
| `GOOGLE_CLIENT_ID` | For Google OAuth | — | From Google Cloud Console |
| `GOOGLE_CLIENT_SECRET` | For Google OAuth | — | From Google Cloud Console |
| `GOOGLE_REDIRECT_URI` | For Google OAuth | `localhost callback` | OAuth redirect URL |
| `GOOGLE_ALLOWLIST` | For Google OAuth | operator emails | Extra Google accounts allowed to sign in |
| `MOBILE_APP_SCHEME` | For native OAuth | `stocksbreakout` in this repo | Custom URL scheme |
| `DATABASE_URL` | No | SQLite auto-created | PostgreSQL connection string |
| `PUSH_TOKEN_FILE` | No | `scanner_output/...` | Expo token storage path |
| `CORS_ORIGINS` | No | first-party + localhost | Allowed origins (`*` refused) |

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `ModuleNotFoundError: trading_api_kit` | Run `pip install ./trading_api_kit` with venv active |
| `401` on all routes | `API_SECRET_KEY` mismatch between token creation and verification |
| `403` on `/admin` | `ADMIN_SECRET` not set or wrong header value |
| Google OAuth redirect fails | `GOOGLE_REDIRECT_URI` in `.env` must match exactly what's in Google Cloud Console |
| Mobile OAuth doesn't redirect back | `MOBILE_APP_SCHEME` in `.env` must match `scheme` in `app.json` |
| `scanner_output/` missing | Auto-created on first run — or `mkdir scanner_output` |

---

*Guide generated: 2026-05-26*  
*Source: [stocksBreakout/trading_api_kit](https://github.com/gilhadas/stocksBreakout/tree/main/trading_api_kit)*
