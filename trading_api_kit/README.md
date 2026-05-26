# trading-api-kit

Reusable FastAPI auth + push layer for trading mobile apps (React Native / Expo).

Extracted from [stocksBreakout](https://github.com/gilhadas/stocksBreakout).  
Drop it into any scanner backend and get JWT auth, Google OAuth, user management, and Expo push notifications in minutes.

---

## What's Included

**Python (FastAPI):**
| Module | What it does |
|--------|-------------|
| `factory.py` | `create_app()` — pre-configured FastAPI with CORS, DB init, routers |
| `auth.py` | JWT create/verify, bcrypt password hashing, legacy single-password |
| `auth_routes.py` | `POST /auth/login`, `GET /auth/me`, `GET /auth/google`, `GET /auth/google/callback` |
| `admin_routes.py` | `GET/POST/DELETE /admin/users`, HTML dashboard at `GET /admin` |
| `push_registry.py` | Expo push token file store (`register_token`, `get_all_tokens`) |
| `deps.py` | `get_current_user` FastAPI dependency |
| `models.py` | SQLAlchemy `User` model (email, password_hash, google_id, name) |
| `database.py` | SQLite / PostgreSQL engine factory, `get_db`, `create_tables` |
| `config.py` | All env-driven settings in one place |

**TypeScript (React Native / Expo):**
| Module | What it does |
|--------|-------------|
| `ts_client/src/client.ts` | `configure()`, `authFetch()`, JWT token storage |
| `ts_client/src/auth.ts` | `loginWithEmail()`, `loginWithPassword()`, `logout()`, `getCurrentUser()`, `getGoogleAuthUrl()` |
| `ts_client/src/notifications.ts` | `registerForPushNotifications()`, `registerPushToken()` |
| `ts_client/src/index.ts` | Single public export |

---

## Quick Start

### Backend (Python)

```python
# my_scanner/api/server.py
from trading_api_kit import create_app, get_current_user
from trading_api_kit.models import User

app = create_app(
    title="MyScanner",
    version="1.0.0",
    static_dir="api/static",   # optional: serve Expo web build
)

# Add your scanner-specific routes
@app.get("/portfolio")
def get_portfolio(user: User = Depends(get_current_user)):
    return {"user": user.email, "positions": [...]}

@app.get("/signals")
def get_signals(user: User = Depends(get_current_user)):
    return {"signals": [...]}
```

```bash
uvicorn my_scanner.api.server:app --host 0.0.0.0 --port 8000
```

### Frontend (TypeScript / Expo)

```typescript
// app/_layout.tsx or similar startup file
import { configure } from 'trading-api-kit';

await configure({ baseUrl: 'https://my-scanner.example.com' });
```

```typescript
// app/login.tsx
import { loginWithEmail, getGoogleAuthUrl, saveToken } from 'trading-api-kit';
import * as WebBrowser from 'expo-web-browser';

// Email login
await loginWithEmail('user@example.com', 'password');

// Google OAuth (native)
const url = await getGoogleAuthUrl('myapp'); // matches MOBILE_APP_SCHEME env
const result = await WebBrowser.openAuthSessionAsync(url, 'myapp://oauth-callback');
if (result.type === 'success') {
  const token = new URL(result.url).searchParams.get('token')!;
  await saveToken(token);
}
```

```typescript
// Calling your custom endpoints
import { authFetch } from 'trading-api-kit';

const portfolio = await authFetch('/portfolio');
const signals = await authFetch('/signals');
```

```typescript
// Push notifications
import { registerForPushNotifications } from 'trading-api-kit';

useEffect(() => { registerForPushNotifications(); }, []);
```

---

## Configuration (.env)

```env
# Required
API_SECRET_KEY=your-random-secret-here    # JWT signing key

# Optional — auth
APP_PASSWORD=legacy-password              # Single-password legacy login
DEFAULT_USER_EMAIL=admin@example.com      # Auto-created default user
DEFAULT_USER_ID=00000000-0000-0000-0000-000000000001

# Optional — Google OAuth
GOOGLE_CLIENT_ID=xxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxx
GOOGLE_REDIRECT_URI=https://my-scanner.example.com/auth/google/callback
MOBILE_APP_SCHEME=myapp                   # Custom URL scheme for native OAuth

# Optional — admin
ADMIN_SECRET=my-admin-secret              # Required to access /admin/*

# Optional — database (defaults to SQLite at scanner_output/users.db)
DATABASE_URL=postgresql://user:pass@localhost/mydb

# Optional — push notifications
PUSH_TOKEN_FILE=scanner_output/.expo_push_tokens.json

# Optional — CORS (defaults to *)
CORS_ORIGINS=https://my-scanner.example.com,https://app.my-scanner.example.com
```

---

## Installation

### Python

```bash
# From PyPI (when published)
pip install trading-api-kit

# From source (local or GitHub)
pip install ./trading_api_kit
pip install "git+https://github.com/gilhadas/stocksBreakout#subdirectory=trading_api_kit"
```

### TypeScript

Copy `ts_client/src/` into your project's `lib/` directory, or:

```bash
# (When published to npm)
npm install trading-api-kit
```

---

## Adapting for a New Scanner

1. **Copy** `trading_api_kit/` to your new repo
2. **Update** `pyproject.toml` name/version
3. **Set** env vars in `.env`
4. **Create** `api/server.py` using `create_app()` and add your scanner endpoints
5. **Copy** `ts_client/src/` into your mobile app's `lib/`
6. **Call** `configure({ baseUrl: '...' })` at startup

The lib is zero-dependency on stocksBreakout internals — no imports from `scanner.py`, `config.py`, or `auto_portfolio.py`.

---

## API Reference

### Python

| Symbol | Description |
|--------|-------------|
| `create_app(title, version, static_dir, include_auth, include_admin)` | FastAPI app factory |
| `get_current_user` | FastAPI dependency → `User` |
| `get_db` | FastAPI dependency → SQLAlchemy session |
| `create_tables()` | Create DB schema |
| `hash_password(plain)` | bcrypt hash |
| `verify_password(plain, hashed)` | bcrypt verify |
| `create_user_token(user_id, email)` | Sign JWT |
| `decode_token(token)` | Verify + decode JWT |
| `register_token(token)` | Save Expo push token |
| `get_all_tokens()` | List all push tokens |

### TypeScript

| Symbol | Description |
|--------|-------------|
| `configure({ baseUrl })` | Set server URL |
| `authFetch(path, opts?)` | Authenticated fetch |
| `loginWithEmail(email, password)` | Email/password login |
| `loginWithPassword(password)` | Legacy single-password login |
| `logout()` | Clear stored token |
| `getCurrentUser()` | `GET /auth/me` |
| `getGoogleAuthUrl(appScheme?)` | Google OAuth URL |
| `getToken()` / `saveToken()` / `clearToken()` | Token storage |
| `isLoggedIn()` | Check if token exists |
| `getEmailFromToken()` | Decode email from JWT |
| `registerForPushNotifications()` | Request permission + register |
| `SessionExpiredError` | Thrown on 401 |
