# StocksBreakout Mobile App

Cross-platform portfolio tracker (iOS, Android, Web) built with Expo + FastAPI.

## Architecture

```
┌──────────────┐     HTTPS      ┌─────────────────┐     import     ┌──────────────────┐
│  Expo App    │ ──────────────→│  FastAPI (local) │ ─────────────→│ auto_portfolio.py │
│  (iOS/And/Web)│  Cloudflare   │  :8000           │               │ (JSON file)       │
└──────────────┘   Tunnel       └─────────────────┘               └──────────────────┘
       ↑                              │
  Expo Push                    notifier.py
  Notifications               (Expo Push channel)
```

## Quick Start

### 1. Start the API Server

```bash
# From project root
uvicorn api.server:app --host 0.0.0.0 --port 8000
```

### 2. Run the Mobile App (Dev)

```bash
cd mobile
npm install        # first time only
npx expo start     # opens Expo dev tools
```

- Press `i` for iOS Simulator, `a` for Android, `w` for web browser
- Or scan the QR code with Expo Go app on your phone

### 3. Expose Remotely (Optional)

```bash
cloudflared tunnel --url http://localhost:8000
```

This gives you a public HTTPS URL (e.g. `https://abc123.trycloudflare.com`) to use from your phone outside the local network.

## Authentication

Single-user password auth with JWT tokens.

| Variable | Location | Purpose |
|---|---|---|
| `APP_PASSWORD` | `.env` | Password to log in (set uniquely in `.env`; no documented default) |
| `API_SECRET_KEY` | `.env` | Signs JWT tokens (required; unique — the API will not boot on a documented default) |

- No signup required — enter the password on the login screen
- JWT token is valid for 30 days, stored locally on device
- To change password: edit `APP_PASSWORD` in `.env` and restart the server

## API Endpoints

Base URL: `http://localhost:8000` (or Cloudflare tunnel URL)

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/auth/login` | POST | No | `{"password": "..."}` → `{"token": "jwt..."}` |
| `/portfolio` | GET | JWT | Returns positions, closed trades, summary stats |
| `/portfolio/refresh` | POST | JWT | Refreshes prices, auto-closes stopped positions |
| `/push/register` | POST | JWT | `{"token": "ExponentPushToken[...]"}` registers device |

### Example: Test with curl

```bash
# Login
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"password":"<APP_PASSWORD>"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")

# Get portfolio
curl -s http://localhost:8000/portfolio -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

### Portfolio Response Shape

```json
{
  "positions": [
    {
      "symbol": "NVDA",
      "entry_price": 118.5,
      "current_price": 129.8,
      "shares": 84,
      "stop": 112.0,
      "target": 132.0,
      "quality": "GOLD",
      "sector": "Technology",
      "date_added": "2026-03-10"
    }
  ],
  "closed": [
    {
      "symbol": "AAPL",
      "entry_price": 220.0,
      "exit_price": 235.5,
      "pnl": 310.0,
      "pnl_pct": 7.05,
      "close_reason": "trailing_stop",
      "hold_days": 5,
      "date_closed": "2026-03-15"
    }
  ],
  "summary": {
    "capital": 10000,
    "cash": 8021.5,
    "total_value": 10260.3,
    "total_pnl": 260.3,
    "unrealized": 65.25,
    "realized": 949.2,
    "open_count": 1,
    "closed_count": 3,
    "win_count": 2
  },
  "last_updated": "2026-03-20T14:30:00-04:00"
}
```

## Push Notifications

Push notifications are sent via Expo Push API when the scanner triggers buy/sell events.

- The mobile app auto-registers its Expo push token on login
- Tokens are stored in `scanner_output/.expo_push_tokens.json`
- `notifier.py` sends pushes via `send_expo_push()` alongside Telegram/Discord
- No Firebase or APNs setup required — Expo handles delivery

## App Screens

### Login
- API URL field (for Cloudflare tunnel or local)
- Password field
- Auto-redirects to portfolio if a valid token exists

### Portfolio Tab
- Summary bar: total value, P&L, cash, open positions, win rate
- List of open positions with entry/current price, P&L%, stop level
- Pull-to-refresh triggers price update + auto-stop checks

### History Tab
- Top stats: realized P&L, win rate, avg hold time
- Scrollable list of closed trades sorted by date
- Each trade shows entry/exit, P&L, hold days, close reason

## Project Structure

```
api/
  auth.py            # JWT create/verify (~40 lines)
  push_registry.py   # Expo push token storage (~30 lines)
  server.py          # FastAPI: 4 endpoints (~95 lines)
mobile/
  app/
    login.tsx        # Password login screen
    _layout.tsx      # Root navigation (login → tabs)
    (tabs)/
      _layout.tsx    # Tab bar config (Portfolio + History)
      index.tsx      # Portfolio dashboard
      two.tsx        # Trade history
  components/
    PositionCard.tsx  # Single position row
    SummaryBar.tsx    # Capital/P&L summary header
  lib/
    api.ts           # Fetch wrapper with JWT auth
    notifications.ts # Expo push registration
```

## Deployment

### API Server (Production)

Run alongside cron_agent on the same Mac:

```bash
# Add to launchd or run in tmux/screen
uvicorn api.server:app --host 0.0.0.0 --port 8000
```

### Cloudflare Tunnel (Stable URL)

For a fixed URL ($5/mo) instead of random tunnel names:

```bash
cloudflared tunnel create stocksbreakout
cloudflared tunnel route dns stocksbreakout portfolio.yourdomain.com
cloudflared tunnel run stocksbreakout --url http://localhost:8000
```

### Mobile Build

```bash
# iOS (TestFlight)
eas build --platform ios

# Android (APK/AAB)
eas build --platform android

# Web (static export)
npx expo export:web
```
