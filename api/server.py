"""
FastAPI server — thin wrapper around auto_portfolio.py.
Run: uvicorn api.server:app --host 0.0.0.0 --port 8000
"""

import sys
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
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


# ── Manual Portfolio ────────────────────────────────────────────────────────

@app.get("/manual-portfolio")
def get_manual_portfolio(_token: str = Depends(_require_auth)):
    import yfinance as yf
    from utils import load_json

    data = load_json('scanner_output/portfolio/portfolio.json')
    if not data:
        return {"positions": [], "closed": [], "cash": 0, "last_updated": ""}

    raw = data.get("positions", {})
    # Normalize: positions is a dict keyed by symbol
    if isinstance(raw, dict):
        pos_list = [{"symbol": k, **v} for k, v in raw.items()]
    else:
        pos_list = raw

    # Fetch current prices in one batch
    symbols = [p["symbol"] for p in pos_list if p.get("symbol")]
    prices = {}
    if symbols:
        try:
            tickers = yf.download(symbols, period="1d", progress=False, auto_adjust=True)
            if not tickers.empty:
                closes = tickers["Close"].iloc[-1]
                for sym in symbols:
                    prices[sym] = float(closes[sym]) if sym in closes else None
        except Exception:
            pass

    def _status(pos, price):
        stop   = pos.get("stop", 0)
        target = pos.get("target", 0)
        if not price:
            return "UNKNOWN"
        if price <= stop:
            return "SELL"
        if target and price >= target:
            return "TARGET"
        if stop and price <= stop * 1.05:
            return "CAUTION"
        return "HOLD"

    positions_out = []
    for pos in pos_list:
        sym   = pos.get("symbol", "")
        price = prices.get(sym) or pos.get("current_price")
        entry = pos.get("entry_price", 0)
        pnl_pct = ((price - entry) / entry * 100) if price and entry else pos.get("unrealized_pnl_pct")
        positions_out.append({
            **pos,
            "current_price": price,
            "pnl_pct":       round(pnl_pct, 2) if pnl_pct is not None else None,
            "status":        _status(pos, price),
        })

    order = {"SELL": 0, "CAUTION": 1, "UNKNOWN": 2, "HOLD": 3, "TARGET": 4}
    positions_out.sort(key=lambda p: order.get(p["status"], 2))

    return {
        "positions":    positions_out,
        "closed":       data.get("trade_history", []),
        "cash":         data.get("cash", 0),
        "last_updated": data.get("last_updated", ""),
    }


# ── Push Notifications ──────────────────────────────────────────────────────

class PushTokenRequest(BaseModel):
    token: str


@app.post("/push/register")
def register_push_token(req: PushTokenRequest, _token: str = Depends(_require_auth)):
    is_new = register_token(req.token)
    return {"ok": True, "new": is_new}


# ── Static Web App (must be last — mounts after all API routes) ─────────────
_dist = Path(__file__).resolve().parent.parent / 'mobile' / 'dist'
if _dist.exists():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="web")
