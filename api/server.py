"""
FastAPI server — thin wrapper around auto_portfolio.py.
Run: uvicorn api.server:app --host 0.0.0.0 --port 8000
"""

import math
import sys
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel


def _clean(obj):
    """Recursively replace nan/inf floats with None so JSON serialization never fails."""
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    return obj

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
    return _clean({
        "positions": data.get("positions", []),
        "closed": data.get("closed", []),
        "summary": summary,
        "last_updated": data.get("last_updated", ""),
    })


@app.post("/portfolio/refresh")
def refresh_portfolio(_token: str = Depends(_require_auth)):
    import auto_portfolio as ap

    result = ap.refresh_prices()
    data = result["data"]
    summary = ap.get_summary(data)
    return _clean({
        "positions": data.get("positions", []),
        "closed": data.get("closed", []),
        "summary": summary,
        "last_updated": data.get("last_updated", ""),
        "auto_closed": result.get("closed", []),
        "updated_count": result.get("updated", 0),
    })


@app.get("/portfolio/skipped")
def get_skipped(_token: str = Depends(_require_auth)):
    import auto_portfolio as ap
    import yfinance as yf

    data = ap.load()
    skipped = data.get("skipped_cash", [])
    capital = data.get("capital", 0)
    cash = ap.available_cash(data)

    # Fetch price history for all unique symbols since earliest date_added
    symbols = list({s["symbol"] for s in skipped if s.get("symbol")})
    # hist_data[sym] = DataFrame with DatetimeIndex, columns Close etc.
    hist_data: dict = {}
    if symbols:
        try:
            tickers = yf.Tickers(" ".join(symbols))
            for sym in symbols:
                try:
                    h = tickers.tickers[sym].history(period="60d")
                    if not h.empty:
                        hist_data[sym] = h
                except Exception:
                    pass
        except Exception:
            pass

    enriched = []
    total_gain = 0.0
    for item in skipped:
        sym = item.get("symbol", "")
        entry = item.get("entry_price", 0) or 0
        stop = item.get("stop", 0) or 0
        shares = item.get("shares", 0) or 0
        date_added = item.get("date_added", "")

        hist = hist_data.get(sym)
        current = entry
        exit_date = None

        if hist is not None and not hist.empty:
            # Only look at bars on/after date_added
            try:
                hist_from = hist[hist.index.strftime('%Y-%m-%d') >= date_added]
            except Exception:
                hist_from = hist
            current = float(hist_from["Close"].iloc[-1]) if not hist_from.empty else entry
            # Find first bar where close <= stop (stop hit date)
            if stop > 0:
                crossed = hist_from[hist_from["Close"] <= stop]
                if not crossed.empty:
                    exit_date = crossed.index[0].strftime('%Y-%m-%d')

        stopped = exit_date is not None
        effective_price = stop if stopped else current
        gain_pct = ((effective_price - entry) / entry * 100) if entry else 0
        gain_dollar = (effective_price - entry) * shares

        total_gain += gain_dollar

        enriched.append({
            **item,
            "current_price": round(current, 2),
            "close_price": round(stop, 2) if stopped else None,
            "exit_date": exit_date,
            "stopped": stopped,
            "gain_pct": round(gain_pct, 2),
            "gain_dollar": round(gain_dollar, 2),
        })

    total_cost = sum(e.get("cost", 0) or 0 for e in enriched)
    total_gain_pct = (total_gain / total_cost * 100) if total_cost else 0
    # Normalized: simulate each trade as $1000 invested
    total_gain_normalized = sum((e.get("gain_pct", 0) / 100) * 1000 for e in enriched)

    # Sort newest-first
    enriched_sorted = sorted(enriched, key=lambda x: x.get("date_added", ""), reverse=True)
    return _clean({
        "skipped": enriched_sorted,
        "count": len(enriched_sorted),
        "capital": capital,
        "available_cash": cash,
        "total_gain": round(total_gain, 2),
        "total_gain_pct": round(total_gain_pct, 2),
        "total_gain_normalized": round(total_gain_normalized, 2),
        "last_updated": data.get("last_updated", ""),
    })


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


def _save_portfolio_json(data: dict):
    """Save portfolio.json to S3 and local fallback."""
    import json, boto3, toml
    from datetime import datetime, timezone, timedelta
    ny_tz = timezone(timedelta(hours=-4))
    data["last_updated"] = datetime.now(ny_tz).isoformat()
    body = json.dumps(data, indent=2)
    local = Path(__file__).resolve().parent.parent / 'scanner_output' / 'portfolio' / 'portfolio.json'
    local.write_text(body)
    secrets_path = Path(__file__).resolve().parent.parent / '.streamlit' / 'secrets.toml'
    try:
        secrets = toml.loads(secrets_path.read_text())
        s3 = boto3.client(
            's3',
            aws_access_key_id     = secrets.get('AWS_ACCESS_KEY_ID') or secrets.get('key'),
            aws_secret_access_key = secrets.get('AWS_SECRET_ACCESS_KEY') or secrets.get('secret'),
            region_name           = secrets.get('AWS_DEFAULT_REGION') or secrets.get('region', 'eu-central-1'),
        )
        s3.put_object(
            Bucket='stocks-breakout-scanner-s3-bucket',
            Key='scanner_output/portfolio/portfolio.json',
            Body=body.encode(),
            ContentType='application/json',
        )
    except Exception:
        pass


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


# ── Manual Portfolio: Sell ──────────────────────────────────────────────────

class SellRequest(BaseModel):
    symbol: str
    exit_price: float


@app.post("/manual-portfolio/sell")
def sell_position(req: SellRequest, _token: str = Depends(_require_auth)):
    from datetime import datetime, timezone, timedelta
    ny_tz = timezone(timedelta(hours=-4))

    data = _load_portfolio_json()
    positions = data.get("positions", {})
    if isinstance(positions, list):
        positions = {p["symbol"]: p for p in positions}

    if req.symbol not in positions:
        raise HTTPException(status_code=404, detail=f"{req.symbol} not found")

    pos = positions.pop(req.symbol)
    entry_price = pos.get("entry_price", 0)
    shares = pos.get("shares", 0)
    pnl = round((req.exit_price - entry_price) * shares, 2)

    entry_date_str = pos.get("entry_date", "")
    try:
        entry_date = datetime.fromisoformat(entry_date_str).date()
        hold_days = (datetime.now(ny_tz).date() - entry_date).days
    except Exception:
        hold_days = 0

    trade = {
        **pos,
        "exit_price": req.exit_price,
        "pnl": pnl,
        "date_closed": datetime.now(ny_tz).strftime("%Y-%m-%d"),
        "hold_days": hold_days,
        "close_reason": "manual",
    }
    trade_history = data.get("trade_history", [])
    trade_history.insert(0, trade)

    data["positions"] = positions
    data["trade_history"] = trade_history
    data["cash"] = round(data.get("cash", 0) + req.exit_price * shares, 2)

    _save_portfolio_json(data)
    return {"ok": True, "pnl": pnl, "cash": data["cash"]}


# ── Manual Portfolio: Buy ────────────────────────────────────────────────────

class BuyRequest(BaseModel):
    symbol: str
    shares: float
    entry_price: float
    stop: float = 0.0
    target: float = 0.0
    sector: str = ""
    broker: str = ""
    mode: str = "swing"


@app.post("/manual-portfolio/buy")
def buy_position(req: BuyRequest, _token: str = Depends(_require_auth)):
    from datetime import datetime, timezone, timedelta
    ny_tz = timezone(timedelta(hours=-4))

    data = _load_portfolio_json()
    positions = data.get("positions", {})
    if isinstance(positions, list):
        positions = {p["symbol"]: p for p in positions}

    cost_basis = round(req.entry_price * req.shares, 2)
    positions[req.symbol] = {
        "symbol":      req.symbol,
        "shares":      req.shares,
        "entry_price": req.entry_price,
        "entry_date":  datetime.now(ny_tz).strftime("%Y-%m-%d"),
        "stop":        req.stop,
        "target":      req.target,
        "sector":      req.sector,
        "broker":      req.broker,
        "mode":        req.mode,
        "cost_basis":  cost_basis,
        "quality":     "",
    }
    data["positions"] = positions
    data["cash"] = round(data.get("cash", 0) - cost_basis, 2)

    _save_portfolio_json(data)
    return {"ok": True, "symbol": req.symbol, "cost_basis": cost_basis, "cash": data["cash"]}


# ── Swap Advisor ────────────────────────────────────────────────────────────

@app.post("/portfolio/suggest-swaps")
def suggest_swaps_endpoint(_token: str = Depends(_require_auth)):
    import auto_portfolio as ap

    swaps = ap.suggest_swaps(notify=True)
    return {"swaps": swaps, "count": len(swaps)}


class ExecuteSwapRequest(BaseModel):
    close_symbol: str
    open_symbol: str


@app.post("/portfolio/execute-swap")
def execute_swap_endpoint(req: ExecuteSwapRequest, _token: str = Depends(_require_auth)):
    import auto_portfolio as ap

    result = ap.execute_swap(req.close_symbol, req.open_symbol)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("reason", "swap failed"))
    return result


@app.post("/portfolio/undo-swap")
def undo_swap_endpoint(_token: str = Depends(_require_auth)):
    import auto_portfolio as ap

    result = ap.undo_last_swap()
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("reason", "undo failed"))
    return result


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
