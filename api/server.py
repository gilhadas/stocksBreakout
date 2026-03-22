"""
FastAPI server — thin wrapper around auto_portfolio.py.
Run: uvicorn api.server:app --host 0.0.0.0 --port 8000
"""

import sys
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.auth import create_token, verify_token
from api.push_registry import register_token

app = FastAPI(title="StocksBreakout Portfolio API", version="1.0")

# Allow Expo web and dev clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_bearer = HTTPBearer()


def _require_auth(creds: HTTPAuthorizationCredentials = Depends(_bearer)):
    if not verify_token(creds.credentials):
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return creds.credentials


# ── Auth ────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    password: str


@app.post("/auth/login")
def login(req: LoginRequest):
    token = create_token(req.password)
    if not token:
        raise HTTPException(status_code=401, detail="Wrong password")
    return {"token": token}


# ── Portfolio ───────────────────────────────────────────────────────────────

@app.get("/portfolio")
def get_portfolio(_token: str = Depends(_require_auth)):
    import auto_portfolio as ap

    data = ap.load()
    summary = ap.get_summary(data)
    return {
        "positions": data.get("positions", []),
        "closed": data.get("closed", []),
        "summary": summary,
        "last_updated": data.get("last_updated", ""),
    }


@app.post("/portfolio/refresh")
def refresh_portfolio(_token: str = Depends(_require_auth)):
    import auto_portfolio as ap

    result = ap.refresh_prices()
    data = result["data"]
    summary = ap.get_summary(data)
    return {
        "positions": data.get("positions", []),
        "closed": data.get("closed", []),
        "summary": summary,
        "last_updated": data.get("last_updated", ""),
        "auto_closed": result.get("closed", []),
        "updated_count": result.get("updated", 0),
    }


# ── Push Notifications ──────────────────────────────────────────────────────

class PushTokenRequest(BaseModel):
    token: str


@app.post("/push/register")
def register_push_token(req: PushTokenRequest, _token: str = Depends(_require_auth)):
    is_new = register_token(req.token)
    return {"ok": True, "new": is_new}
