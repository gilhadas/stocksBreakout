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

def _load_portfolio_json() -> dict:
    """Load portfolio.json from S3 (with boto3) or local fallback."""
    import json, boto3, toml
    secrets_path = Path(__file__).resolve().parent.parent / '.streamlit' / 'secrets.toml'
    try:
        secrets = toml.loads(secrets_path.read_text())
        s3 = boto3.client(
            's3',
            aws_access_key_id     = secrets.get('AWS_ACCESS_KEY_ID') or secrets.get('key'),
            aws_secret_access_key = secrets.get('AWS_SECRET_ACCESS_KEY') or secrets.get('secret'),
            region_name           = secrets.get('AWS_DEFAULT_REGION') or secrets.get('region', 'eu-central-1'),
        )
        obj = s3.get_object(
            Bucket = 'stocks-breakout-scanner-s3-bucket',
            Key    = 'scanner_output/portfolio/portfolio.json',
        )
        return json.loads(obj['Body'].read().decode())
    except Exception as e:
        # Fall back to local file
        local = Path(__file__).resolve().parent.parent / 'scanner_output' / 'portfolio' / 'portfolio.json'
        if local.exists():
            return json.loads(local.read_text())
        return {}


@app.get("/manual-portfolio")
def get_manual_portfolio(_token: str = Depends(_require_auth)):
    import yfinance as yf

    data = _load_portfolio_json()
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


# ── Manual Portfolio: Compute Stops ─────────────────────────────────────────

@app.post("/manual-portfolio/compute-stops")
def compute_stops(_token: str = Depends(_require_auth)):
    """Fetch 60-day OHLC for each position, compute ATR14 + swing-low stop, save back to S3."""
    import json, boto3, toml, yfinance as yf, pandas as pd
    import numpy as np
    from datetime import datetime, timezone, timedelta

    secrets_path = Path(__file__).resolve().parent.parent / '.streamlit' / 'secrets.toml'
    secrets = toml.loads(secrets_path.read_text())

    def _s3_client():
        return boto3.client(
            's3',
            aws_access_key_id     = secrets.get('AWS_ACCESS_KEY_ID') or secrets.get('key'),
            aws_secret_access_key = secrets.get('AWS_SECRET_ACCESS_KEY') or secrets.get('secret'),
            region_name           = secrets.get('AWS_DEFAULT_REGION', 'eu-central-1'),
        )

    data = _load_portfolio_json()
    if not data:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    raw = data.get("positions", {})
    pos_dict = raw if isinstance(raw, dict) else {p["symbol"]: p for p in raw}
    symbols  = list(pos_dict.keys())

    SL_MULT  = 3.0   # matches swing mode default
    LOOKBACK = 20    # bars for swing low

    # Download 60 days of daily OHLC in one batch
    hist = yf.download(symbols, period="60d", progress=False, auto_adjust=True)

    updated = {}
    for sym in symbols:
        pos = pos_dict[sym]
        try:
            if len(symbols) == 1:
                df = hist.copy()
            else:
                df = hist.xs(sym, axis=1, level=1).dropna() if sym in hist.columns.get_level_values(1) else None

            if df is None or len(df) < 15:
                updated[sym] = {**pos, "stop_note": "insufficient data"}
                continue

            # ATR14
            hl  = df["High"] - df["Low"]
            hc  = (df["High"] - df["Close"].shift()).abs()
            lc  = (df["Low"]  - df["Close"].shift()).abs()
            atr = pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(14).mean().iloc[-1]

            price      = float(df["Close"].iloc[-1])
            atr_stop   = price - SL_MULT * float(atr)
            swing_low  = float(df["Low"].iloc[-LOOKBACK:].min())
            stop       = round(max(swing_low, atr_stop), 2)

            updated[sym] = {**pos, "stop": stop, "stop_note": f"ATR14={atr:.2f} swing_low={swing_low:.2f}"}
        except Exception as e:
            updated[sym] = {**pos, "stop_note": str(e)}

    ny_tz = timezone(timedelta(hours=-4))
    data["positions"]    = updated
    data["last_updated"] = datetime.now(ny_tz).isoformat()

    body = json.dumps(data, indent=2)
    # Save locally
    local = Path(__file__).resolve().parent.parent / 'scanner_output' / 'portfolio' / 'portfolio.json'
    local.write_text(body)
    # Save to S3
    try:
        _s3_client().put_object(
            Bucket      = 'stocks-breakout-scanner-s3-bucket',
            Key         = 'scanner_output/portfolio/portfolio.json',
            Body        = body.encode(),
            ContentType = 'application/json',
        )
    except Exception as e:
        pass

    return {
        "updated": len(updated),
        "stops": {s: p.get("stop") for s, p in updated.items()},
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
