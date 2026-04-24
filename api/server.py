"""
FastAPI server — thin wrapper around auto_portfolio.py.
Run: uvicorn api.server:app --host 0.0.0.0 --port 8000
"""

import math
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.admin_routes import router as admin_router
from api.auth_routes import router as auth_router
from api.database import create_tables
from api.deps import get_current_user
from api.models import User
from api.push_registry import register_token


def _clean(obj):
    """Recursively coerce numpy/pandas scalars and nan/inf floats for JSON serialization."""
    import numpy as np
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    return obj


def _ensure_default_user():
    """Create the default user row on first startup so legacy JWT tokens resolve correctly."""
    from api.database import SessionLocal
    email = os.getenv('DEFAULT_USER_EMAIL', '')
    user_id = os.getenv('DEFAULT_USER_ID', '')
    if not email or not user_id:
        return
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == email).first()
        if existing is None:
            from datetime import datetime, timezone
            db.add(User(
                id=user_id,
                email=email,
                name=email.split('@')[0],
                created_at=datetime.now(timezone.utc),
            ))
            db.commit()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    _ensure_default_user()
    yield


app = FastAPI(title="StocksBreakout Portfolio API", version="2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(admin_router)


# ── Manual portfolio helpers (user-scoped) ───────────────────────────────────

def _portfolio_key(user_id: str) -> tuple[str, Path]:
    """Return (s3_key, local_path) for this user's portfolio.json."""
    default_id = os.getenv('DEFAULT_USER_ID', '')
    root = Path(__file__).resolve().parent.parent
    if not user_id or user_id == default_id:
        s3_key = 'scanner_output/portfolio/portfolio.json'
        local = root / 'scanner_output' / 'portfolio' / 'portfolio.json'
    else:
        s3_key = f'scanner_output/portfolio/{user_id}/portfolio.json'
        local = root / 'scanner_output' / 'portfolio' / user_id / 'portfolio.json'
    return s3_key, local


def _load_portfolio_json(user_id: str) -> dict:
    import json, boto3, toml
    s3_key, local = _portfolio_key(user_id)
    secrets_path = Path(__file__).resolve().parent.parent / '.streamlit' / 'secrets.toml'
    try:
        secrets = toml.loads(secrets_path.read_text())
        s3 = boto3.client(
            's3',
            aws_access_key_id     = secrets.get('AWS_ACCESS_KEY_ID') or secrets.get('key'),
            aws_secret_access_key = secrets.get('AWS_SECRET_ACCESS_KEY') or secrets.get('secret'),
            region_name           = secrets.get('AWS_DEFAULT_REGION') or secrets.get('region', 'eu-central-1'),
        )
        obj = s3.get_object(Bucket='stocks-breakout-scanner-s3-bucket', Key=s3_key)
        return json.loads(obj['Body'].read().decode())
    except Exception:
        if local.exists():
            return json.loads(local.read_text())
        return {}


def _save_portfolio_json(data: dict, user_id: str):
    import json, boto3, toml
    from datetime import datetime, timezone, timedelta
    ny_tz = timezone(timedelta(hours=-4))
    data["last_updated"] = datetime.now(ny_tz).isoformat()
    body = json.dumps(data, indent=2)
    s3_key, local = _portfolio_key(user_id)
    local.parent.mkdir(parents=True, exist_ok=True)
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
            Key=s3_key,
            Body=body.encode(),
            ContentType='application/json',
        )
    except Exception:
        pass


# ── Portfolio ────────────────────────────────────────────────────────────────

@app.get("/portfolio")
def get_portfolio(current_user: User = Depends(get_current_user)):
    import auto_portfolio as ap

    data = ap.load(user_id=current_user.id)
    summary = ap.get_summary(data)
    return _clean({
        "positions":    data.get("positions", []),
        "closed":       data.get("closed", []),
        "summary":      summary,
        "last_updated": data.get("last_updated", ""),
    })


@app.post("/portfolio/refresh")
def refresh_portfolio(current_user: User = Depends(get_current_user)):
    import auto_portfolio as ap

    result = ap.refresh_prices(user_id=current_user.id)
    data = result["data"]
    summary = ap.get_summary(data)
    return _clean({
        "positions":    data.get("positions", []),
        "closed":       data.get("closed", []),
        "summary":      summary,
        "last_updated": data.get("last_updated", ""),
        "auto_closed":  result.get("closed", []),
        "updated_count": result.get("updated", 0),
    })


@app.get("/portfolio/skipped")
def get_skipped(current_user: User = Depends(get_current_user)):
    import auto_portfolio as ap
    import yfinance as yf

    data = ap.load(user_id=current_user.id)
    skipped = data.get("skipped_cash", [])
    capital = data.get("capital", 0)
    cash = ap.available_cash(data)

    symbols = list({s["symbol"] for s in skipped if s.get("symbol")})
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
            try:
                hist_from = hist[hist.index.strftime('%Y-%m-%d') >= date_added]
            except Exception:
                hist_from = hist
            current = float(hist_from["Close"].iloc[-1]) if not hist_from.empty else entry
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
            "close_price":   round(stop, 2) if stopped else None,
            "exit_date":     exit_date,
            "stopped":       stopped,
            "gain_pct":      round(gain_pct, 2),
            "gain_dollar":   round(gain_dollar, 2),
        })

    total_cost = sum(e.get("cost", 0) or 0 for e in enriched)
    total_gain_pct = (total_gain / total_cost * 100) if total_cost else 0
    total_gain_normalized = sum((e.get("gain_pct", 0) / 100) * 1000 for e in enriched)

    enriched_sorted = sorted(enriched, key=lambda x: x.get("date_added", ""), reverse=True)
    return _clean({
        "skipped":               enriched_sorted,
        "count":                 len(enriched_sorted),
        "capital":               capital,
        "available_cash":        cash,
        "total_gain":            round(total_gain, 2),
        "total_gain_pct":        round(total_gain_pct, 2),
        "total_gain_normalized": round(total_gain_normalized, 2),
        "last_updated":          data.get("last_updated", ""),
    })


# ── Manual Portfolio ─────────────────────────────────────────────────────────

@app.get("/manual-portfolio")
def get_manual_portfolio(current_user: User = Depends(get_current_user)):
    import yfinance as yf

    data = _load_portfolio_json(current_user.id)
    if not data:
        return {"positions": [], "closed": [], "cash": 0, "last_updated": ""}

    raw = data.get("positions", {})
    pos_list = [{"symbol": k, **v} for k, v in raw.items()] if isinstance(raw, dict) else raw

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
        stop = pos.get("stop", 0)
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
        sym = pos.get("symbol", "")
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


@app.post("/manual-portfolio/compute-stops")
def compute_stops(current_user: User = Depends(get_current_user)):
    import json, boto3, toml, yfinance as yf, pandas as pd

    secrets_path = Path(__file__).resolve().parent.parent / '.streamlit' / 'secrets.toml'
    secrets = toml.loads(secrets_path.read_text())

    def _s3_client():
        return boto3.client(
            's3',
            aws_access_key_id     = secrets.get('AWS_ACCESS_KEY_ID') or secrets.get('key'),
            aws_secret_access_key = secrets.get('AWS_SECRET_ACCESS_KEY') or secrets.get('secret'),
            region_name           = secrets.get('AWS_DEFAULT_REGION', 'eu-central-1'),
        )

    data = _load_portfolio_json(current_user.id)
    if not data:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    raw = data.get("positions", {})
    pos_dict = raw if isinstance(raw, dict) else {p["symbol"]: p for p in raw}
    symbols = list(pos_dict.keys())

    SL_MULT = 3.0
    LOOKBACK = 20

    hist = yf.download(symbols, period="60d", progress=False, auto_adjust=True)

    updated = {}
    for sym in symbols:
        pos = pos_dict[sym]
        try:
            df = hist.copy() if len(symbols) == 1 else (
                hist.xs(sym, axis=1, level=1).dropna()
                if sym in hist.columns.get_level_values(1) else None
            )
            if df is None or len(df) < 15:
                updated[sym] = {**pos, "stop_note": "insufficient data"}
                continue

            hl = df["High"] - df["Low"]
            hc = (df["High"] - df["Close"].shift()).abs()
            lc = (df["Low"]  - df["Close"].shift()).abs()
            atr = pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(14).mean().iloc[-1]

            price = float(df["Close"].iloc[-1])
            atr_stop = price - SL_MULT * float(atr)
            swing_low = float(df["Low"].iloc[-LOOKBACK:].min())
            stop = round(max(swing_low, atr_stop), 2)

            updated[sym] = {**pos, "stop": stop, "stop_note": f"ATR14={atr:.2f} swing_low={swing_low:.2f}"}
        except Exception as e:
            updated[sym] = {**pos, "stop_note": str(e)}

    from datetime import datetime, timezone, timedelta
    ny_tz = timezone(timedelta(hours=-4))
    data["positions"] = updated
    data["last_updated"] = datetime.now(ny_tz).isoformat()

    body = json.dumps(data, indent=2)
    s3_key, local = _portfolio_key(current_user.id)
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(body)
    try:
        _s3_client().put_object(
            Bucket='stocks-breakout-scanner-s3-bucket',
            Key=s3_key,
            Body=body.encode(),
            ContentType='application/json',
        )
    except Exception:
        pass

    return {"updated": len(updated), "stops": {s: p.get("stop") for s, p in updated.items()}}


class SellRequest(BaseModel):
    symbol: str
    exit_price: float


@app.post("/manual-portfolio/sell")
def sell_position(req: SellRequest, current_user: User = Depends(get_current_user)):
    from datetime import datetime, timezone, timedelta
    ny_tz = timezone(timedelta(hours=-4))

    data = _load_portfolio_json(current_user.id)
    positions = data.get("positions", {})
    if isinstance(positions, list):
        positions = {p["symbol"]: p for p in positions}

    if req.symbol not in positions:
        raise HTTPException(status_code=404, detail=f"{req.symbol} not found")

    pos = positions.pop(req.symbol)
    entry_price = pos.get("entry_price", 0)
    shares = pos.get("shares", 0)
    pnl = round((req.exit_price - entry_price) * shares, 2)

    try:
        hold_days = (datetime.now(ny_tz).date() - datetime.fromisoformat(pos.get("entry_date", "")).date()).days
    except Exception:
        hold_days = 0

    trade_history = data.get("trade_history", [])
    trade_history.insert(0, {
        **pos,
        "exit_price":   req.exit_price,
        "pnl":          pnl,
        "date_closed":  datetime.now(ny_tz).strftime("%Y-%m-%d"),
        "hold_days":    hold_days,
        "close_reason": "manual",
    })
    data["positions"] = positions
    data["trade_history"] = trade_history
    data["cash"] = round(data.get("cash", 0) + req.exit_price * shares, 2)

    _save_portfolio_json(data, current_user.id)
    return {"ok": True, "pnl": pnl, "cash": data["cash"]}


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
def buy_position(req: BuyRequest, current_user: User = Depends(get_current_user)):
    from datetime import datetime, timezone, timedelta
    ny_tz = timezone(timedelta(hours=-4))

    data = _load_portfolio_json(current_user.id)
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

    _save_portfolio_json(data, current_user.id)
    return {"ok": True, "symbol": req.symbol, "cost_basis": cost_basis, "cash": data["cash"]}


# ── Swap Advisor ─────────────────────────────────────────────────────────────

@app.post("/portfolio/suggest-swaps")
def suggest_swaps_endpoint(current_user: User = Depends(get_current_user)):
    import auto_portfolio as ap

    swaps = ap.suggest_swaps(user_id=current_user.id, notify=True)
    return {"swaps": swaps, "count": len(swaps)}


class ExecuteSwapRequest(BaseModel):
    close_symbol: str
    open_symbol: str


@app.post("/portfolio/execute-swap")
def execute_swap_endpoint(req: ExecuteSwapRequest, current_user: User = Depends(get_current_user)):
    import auto_portfolio as ap

    result = ap.execute_swap(req.close_symbol, req.open_symbol, user_id=current_user.id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("reason", "swap failed"))
    return result


@app.post("/portfolio/undo-swap")
def undo_swap_endpoint(current_user: User = Depends(get_current_user)):
    import auto_portfolio as ap

    result = ap.undo_last_swap(user_id=current_user.id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("reason", "undo failed"))
    return result


@app.post("/portfolio/reset")
def portfolio_reset_endpoint(current_user: User = Depends(get_current_user)):
    import auto_portfolio as ap

    data = ap.reset(user_id=current_user.id)
    return {"ok": True, "capital": data.get("capital", 0)}


class RecalculateRequest(BaseModel):
    min_date: str | None = None
    position_pct: float | None = None


@app.post("/portfolio/recalculate")
def portfolio_recalculate_endpoint(
    req: RecalculateRequest,
    current_user: User = Depends(get_current_user),
):
    import auto_portfolio as ap
    from config import PORTFOLIO

    pct = req.position_pct if req.position_pct else PORTFOLIO.get("position_pct", 0.10)
    result = ap.recalculate(position_pct=pct, min_date=req.min_date, user_id=current_user.id)
    summary = ap.get_summary(result["data"])
    summary["files_scanned"] = result.get("files_scanned", 0)
    summary["added"] = result.get("added", 0)
    return _clean(summary)


# ── Push Notifications ───────────────────────────────────────────────────────

class PushTokenRequest(BaseModel):
    token: str


@app.post("/push/register")
def register_push_token(req: PushTokenRequest, current_user: User = Depends(get_current_user)):
    is_new = register_token(req.token)
    return {"ok": True, "new": is_new}


# ── Single-Symbol Analysis ───────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    symbol: str
    mode: str = "swing"
    timeframe: str = "1 day"


@app.post("/analyze")
async def analyze_endpoint(req: AnalyzeRequest, current_user: User = Depends(get_current_user)):
    from api.analyze import analyze_symbol

    try:
        report = await analyze_symbol(req.symbol, req.mode, req.timeframe)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _clean(report)


class AnalyzeChatRequest(BaseModel):
    report: dict
    history: list = []
    question: str


@app.post("/analyze/chat")
async def analyze_chat_endpoint(req: AnalyzeChatRequest, current_user: User = Depends(get_current_user)):
    if not os.getenv('ANTHROPIC_API_KEY'):
        raise HTTPException(status_code=501, detail="LLM not configured (ANTHROPIC_API_KEY missing)")

    from api.llm_chat import chat as llm_chat

    try:
        answer = await llm_chat(req.report, req.history, req.question)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"answer": answer}


@app.get("/analyze/llm-status")
def analyze_llm_status(current_user: User = Depends(get_current_user)):
    return {"enabled": bool(os.getenv('ANTHROPIC_API_KEY'))}


# ── Static Web App (must be last — mounts after all API routes) ──────────────
_dist = Path(__file__).resolve().parent.parent / 'mobile' / 'dist'
if _dist.exists():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="web")
